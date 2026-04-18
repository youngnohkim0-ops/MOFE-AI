"""
멀티세션 RAG 챗봇 — Supabase(세션·벡터) + OpenAI(gpt-4o-mini, embeddings) + Streamlit
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import streamlit as st
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from supabase import Client, create_client

# --- 경로: AI-Education 루트 기준 ---
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"


def _resolve_log_dir() -> Path:
    """로컬은 REPO_ROOT/logs, Streamlit Cloud 등 읽기 전용 환경은 /tmp 하위 사용."""
    candidates = (
        REPO_ROOT / "logs",
        Path(tempfile.gettempdir()) / "multi-session-ref-logs",
    )
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    return Path(tempfile.gettempdir())


LOG_DIR = _resolve_log_dir()

EMBED_MODEL = "text-embedding-3-small"
LLM_MODEL = "gpt-4o-mini"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
VECTOR_BATCH = 10
RAG_TOP_K = 10
MEMORY_TURNS = 50

# --- 로깅 (ref.txt: ERROR/WARNING만, HTTP 로그 억제) ---
for noisy in ("httpx", "httpcore", "urllib3", "openai", "langchain", "langchain_openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

_log_handlers: list[logging.Handler] = []
try:
    _log_path = LOG_DIR / f"chatbot_{datetime.now().strftime('%Y%m%d')}.log"
    _log_handlers.append(logging.FileHandler(_log_path, encoding="utf-8"))
except OSError:
    _log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=_log_handlers,
)


def load_env() -> None:
    load_dotenv(ENV_PATH, override=True)


def env_ok() -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.getenv("SUPABASE_URL"):
        missing.append("SUPABASE_URL")
    if not os.getenv("SUPABASE_ANON_KEY"):
        missing.append("SUPABASE_ANON_KEY")
    return (len(missing) == 0, missing)


def get_supabase() -> Client | None:
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def get_llm(temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(model=LLM_MODEL, temperature=temperature, streaming=True)


def get_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBED_MODEL)


def remove_separators(text: str) -> str:
    if not text:
        return text
    out = re.sub(r"~~[^~]+~~", "", text)
    out = re.sub(r"^[\s]*[-_=]{3,}[\s]*$", "", out, flags=re.MULTILINE)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _messages_to_lc(history: list[dict[str, str]], limit: int = MEMORY_TURNS) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    tail = history[-limit:] if len(history) > limit else history
    for m in tail:
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    return msgs


def ensure_session_row(sb: Client, title: str = "작업 중") -> str:
    if st.session_state.get("session_id"):
        return st.session_state.session_id
    sid = str(uuid.uuid4())
    sb.table("sessions").insert({"id": sid, "title": title}).execute()
    st.session_state.session_id = sid
    st.session_state.saved_msg_count = 0
    return sid


def list_sessions(sb: Client) -> list[dict[str, Any]]:
    r = sb.table("sessions").select("id,title,updated_at").order("updated_at", desc=True).execute()
    return r.data or []


def session_label(s: dict[str, Any]) -> str:
    tid = str(s["id"])[:8]
    return f"{s.get('title', '제목 없음')} ({tid})"


def load_messages_from_db(sb: Client, session_id: str) -> list[dict[str, str]]:
    r = (
        sb.table("chat_messages")
        .select("role,content,msg_order")
        .eq("session_id", session_id)
        .order("msg_order")
        .execute()
    )
    rows = r.data or []
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def sync_chat_to_db(sb: Client, session_id: str, chat_history: list[dict[str, str]]) -> None:
    sb.table("chat_messages").delete().eq("session_id", session_id).execute()
    now = datetime.now(timezone.utc).isoformat()
    for i, m in enumerate(chat_history):
        sb.table("chat_messages").insert(
            {
                "session_id": session_id,
                "role": m["role"],
                "content": m["content"],
                "msg_order": i,
            }
        ).execute()
    sb.table("sessions").update({"updated_at": now}).eq("id", session_id).execute()


def delete_session_cascade(sb: Client, session_id: str) -> None:
    sb.table("sessions").delete().eq("id", session_id).execute()


def _normalize_embedding(val: Any) -> list[float]:
    if val is None:
        raise ValueError("embedding is null")
    if isinstance(val, list):
        return [float(x) for x in val]
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return [float(x) for x in parts]
    raise TypeError(f"unsupported embedding type: {type(val)}")


def clone_session_snapshot(sb: Client, source_id: str, new_title: str) -> str:
    new_id = str(uuid.uuid4())
    sb.table("sessions").insert({"id": new_id, "title": new_title}).execute()
    msgs = (
        sb.table("chat_messages")
        .select("role,content,msg_order")
        .eq("session_id", source_id)
        .order("msg_order")
        .execute()
    )
    for row in msgs.data or []:
        sb.table("chat_messages").insert(
            {
                "session_id": new_id,
                "role": row["role"],
                "content": row["content"],
                "msg_order": row["msg_order"],
            }
        ).execute()
    vecs = sb.table("vector_documents").select("*").eq("session_id", source_id).execute()
    batch: list[dict[str, Any]] = []
    for row in vecs.data or []:
        emb_raw = row.get("embedding")
        batch.append(
            {
                "session_id": new_id,
                "file_name": row["file_name"],
                "content": row.get("content"),
                "metadata": row.get("metadata") or {},
                "embedding": _normalize_embedding(emb_raw),
            }
        )
        if len(batch) >= VECTOR_BATCH:
            sb.table("vector_documents").insert(batch).execute()
            batch = []
    if batch:
        sb.table("vector_documents").insert(batch).execute()
    return new_id


def generate_session_title(chat_history: list[dict[str, str]]) -> str:
    q, a = None, None
    for i in range(len(chat_history) - 1):
        if chat_history[i]["role"] == "user" and chat_history[i + 1]["role"] == "assistant":
            q, a = chat_history[i]["content"], chat_history[i + 1]["content"]
            break
    if not q or not a:
        return "새 세션"
    prompt = (
        "다음은 채팅의 첫 질문과 첫 답변입니다. 이 대화를 대표하는 짧은 세션 제목을 "
        "한국어로 30자 이내 한 줄로만 출력하세요. 따옴표나 부가 설명 없이 제목만.\n\n"
        f"질문:\n{q}\n\n답변:\n{a[:1200]}"
    )
    title_llm = ChatOpenAI(model=LLM_MODEL, temperature=0.3, streaming=False)
    inv = title_llm.invoke(prompt)
    title = getattr(inv, "content", str(inv)).strip().split("\n")[0][:80]
    return title or "새 세션"


def retrieve_with_rpc(
    sb: Client, embeddings: OpenAIEmbeddings, query: str, session_id: str, k: int = RAG_TOP_K
) -> list[tuple[str, str]]:
    """Returns list of (file_name, content snippet)."""
    q_emb = embeddings.embed_query(query)
    try:
        res = sb.rpc(
            "match_vector_documents",
            {
                "query_embedding": q_emb,
                "match_count": k,
                "filter_session_id": session_id,
            },
        ).execute()
        rows = res.data or []
        return [(r.get("file_name") or "", (r.get("content") or "").strip()) for r in rows]
    except Exception as e:
        logging.warning("RPC match_vector_documents failed: %s", e)
        return _retrieve_fallback(sb, embeddings, q_emb, session_id, k)


def _retrieve_fallback(
    sb: Client, embeddings: OpenAIEmbeddings, q_emb: list[float], session_id: str, k: int
) -> list[tuple[str, str]]:
    import numpy as np

    r = sb.table("vector_documents").select("file_name,content,embedding").eq("session_id", session_id).limit(500).execute()
    rows = r.data or []
    if not rows:
        return []
    q = np.array(q_emb, dtype=np.float64)
    qn = np.linalg.norm(q) or 1.0
    scored: list[tuple[float, str, str]] = []
    for row in rows:
        raw = row.get("embedding")
        if raw is None:
            continue
        if isinstance(raw, str):
            vec = np.array([float(x) for x in raw.strip("[]").split(",")], dtype=np.float64)
        else:
            vec = np.array(raw, dtype=np.float64)
        vn = np.linalg.norm(vec) or 1.0
        sim = float(np.dot(q, vec) / (qn * vn))
        scored.append((sim, row.get("file_name") or "", (row.get("content") or "").strip()))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(fn, txt) for _, fn, txt in scored[:k]]


def insert_pdf_vectors(
    sb: Client,
    embeddings: OpenAIEmbeddings,
    session_id: str,
    file_name: str,
    chunks: list[str],
    file_hash: str,
) -> None:
    rows: list[dict[str, Any]] = []
    for i, text in enumerate(chunks):
        rows.append(
            {
                "content": text,
                "metadata": {"file_hash": file_hash, "chunk_index": i, "source": file_name},
            }
        )
    for start in range(0, len(rows), VECTOR_BATCH):
        batch = rows[start : start + VECTOR_BATCH]
        texts = [b["content"] for b in batch]
        embs = embeddings.embed_documents(texts)
        payload = []
        for j, b in enumerate(batch):
            payload.append(
                {
                    "session_id": session_id,
                    "file_name": file_name,
                    "content": b["content"],
                    "metadata": b["metadata"],
                    "embedding": embs[j],
                }
            )
        sb.table("vector_documents").insert(payload).execute()


def has_file_embedding(sb: Client, session_id: str, file_hash: str) -> bool:
    r = (
        sb.table("vector_documents")
        .select("metadata")
        .eq("session_id", session_id)
        .limit(2000)
        .execute()
    )
    for row in r.data or []:
        md = row.get("metadata") or {}
        if md.get("file_hash") == file_hash:
            return True
    return False


def list_vector_file_names(sb: Client, session_id: str) -> list[str]:
    r = sb.table("vector_documents").select("file_name").eq("session_id", session_id).execute()
    names = sorted({row["file_name"] for row in (r.data or []) if row.get("file_name")})
    return names


def load_embedding_file_hashes(sb: Client, session_id: str) -> set[str]:
    r = sb.table("vector_documents").select("metadata").eq("session_id", session_id).limit(5000).execute()
    out: set[str] = set()
    for row in r.data or []:
        fh = (row.get("metadata") or {}).get("file_hash")
        if isinstance(fh, str):
            out.add(fh)
    return out


def build_system_prompt(rag_context: str | None) -> str:
    base = (
        "당신은 친절한 한국어 어시스턴트입니다. 답변은 반드시 마크다운 헤딩(# ## ###)으로 구조화하고 "
        "존댓말로 완전한 문장으로 작성합니다. 구분선(---, ===, ___)과 취소선(~~)은 사용하지 마세요. "
        "출처·참조 문구는 넣지 마세요."
    )
    if rag_context:
        base += (
            "\n\n아래는 검색된 문서 발췌입니다. 이를 바탕으로 답하되, 문서에 없으면 그 사실을 알립니다.\n\n"
            f"{rag_context}"
        )
    return base


def stream_answer(
    llm: ChatOpenAI,
    system_text: str,
    history_lc: list[BaseMessage],
    user_text: str,
) -> Generator[str, None, None]:
    msgs: list[BaseMessage] = [SystemMessage(content=system_text), *history_lc, HumanMessage(content=user_text)]
    for chunk in llm.stream(msgs):
        c = getattr(chunk, "content", None)
        if c:
            yield c


def make_followup_block(answer_text: str, user_q: str) -> str:
    fol = ChatOpenAI(model=LLM_MODEL, temperature=0.5, streaming=False)
    inv = fol.invoke(
        "다음 질문과 답변을 읽고, 사용자가 이어서 물어보면 좋을 질문을 한국어로 정확히 3개만 작성하세요. "
        "형식은 반드시:\n1. ...\n2. ...\n3. ...\n\n"
        f"질문: {user_q}\n\n답변: {answer_text[:4000]}"
    )
    body = getattr(inv, "content", str(inv)).strip()
    return "### 💡 다음에 물어볼 수 있는 질문들\n\n" + body


def reset_ui_state(sb: Client | None) -> None:
    if sb:
        sid = str(uuid.uuid4())
        sb.table("sessions").insert({"id": sid, "title": "작업 중"}).execute()
        st.session_state.session_id = sid
    else:
        st.session_state.session_id = str(uuid.uuid4())
    st.session_state.chat_history = []
    st.session_state.saved_msg_count = 0
    st.session_state.processed_files = []
    st.session_state.file_hashes = set()
    st.session_state.pending_reply = False


def main() -> None:
    load_env()
    ok, missing = env_ok()

    st.set_page_config(page_title="멀티세션 RAG 챗봇", page_icon="📚", layout="wide")

    st.markdown(
        """
<style>
h1 { color: #ff69b4 !important; font-size: 1.4rem !important; }
h2 { color: #ffd700 !important; font-size: 1.2rem !important; }
h3 { color: #1f77b4 !important; font-size: 1.1rem !important; }
div.stButton > button:first-child { background-color: #ff69b4; color: white; }
</style>
""",
        unsafe_allow_html=True,
    )

    col_logo, col_title, _ = st.columns([1, 4, 1])
    logo_path = REPO_ROOT / "logo.png"
    with col_logo:
        if logo_path.exists():
            st.image(str(logo_path), width=180)
        else:
            st.markdown("### 📚")
    with col_title:
        st.markdown(
            """
<div style="text-align:center;">
  <span style="font-size:4rem !important; font-weight:700;">
    <span style="color:#1f77b4 !important;">멀티세션</span>
    <span style="color:#ffd700 !important;"> RAG 챗봇</span>
  </span>
</div>
""",
            unsafe_allow_html=True,
        )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = None
    if "saved_msg_count" not in st.session_state:
        st.session_state.saved_msg_count = 0
    if "processed_files" not in st.session_state:
        st.session_state.processed_files = []
    if "file_hashes" not in st.session_state:
        st.session_state.file_hashes = set()
    if "vectordb_open" not in st.session_state:
        st.session_state.vectordb_open = False
    if "pending_reply" not in st.session_state:
        st.session_state.pending_reply = False

    sb = get_supabase()
    if ok and sb is not None:
        ensure_session_row(sb)

    with st.sidebar:
        if not ok:
            st.warning("환경 변수가 누락되었습니다: " + ", ".join(missing))
        elif sb is None:
            st.warning("Supabase 클라이언트를 만들 수 없습니다. 키를 확인하세요.")

        st.markdown("### LLM")
        st.radio("모델", [LLM_MODEL], index=0, disabled=True)

        st.markdown("### 세션 관리")
        session_options: list[str] = []
        id_by_label: dict[str, str] = {}
        if sb:
            sessions = list_sessions(sb)
            session_options = [session_label(s) for s in sessions]
            id_by_label = {session_label(s): str(s["id"]) for s in sessions}

        def on_session_pick() -> None:
            if not sb:
                return
            lab = st.session_state.get("_sess_sel")
            if not lab or lab.startswith("("):
                return
            if lab not in id_by_label:
                return
            sid = id_by_label[lab]
            st.session_state.session_id = sid
            st.session_state.chat_history = load_messages_from_db(sb, sid)
            st.session_state.saved_msg_count = len(st.session_state.chat_history)
            st.session_state.processed_files = list_vector_file_names(sb, sid)
            st.session_state.file_hashes = load_embedding_file_hashes(sb, sid)
            st.session_state.pending_reply = False

        current_label = None
        if sb and st.session_state.session_id and id_by_label:
            for lab, sid in id_by_label.items():
                if sid == st.session_state.session_id:
                    current_label = lab
                    break

        st.selectbox(
            "세션 선택",
            options=session_options if session_options else ["(저장된 세션 없음)"],
            index=session_options.index(current_label) if current_label in session_options else 0,
            key="_sess_sel",
            on_change=on_session_pick,
            disabled=not sb or not session_options,
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("세션저장") and sb:
                sid = st.session_state.session_id or ensure_session_row(sb)
                if not st.session_state.chat_history:
                    st.error("저장할 대화가 없습니다.")
                else:
                    sync_chat_to_db(sb, sid, st.session_state.chat_history)
                    title = generate_session_title(st.session_state.chat_history)
                    new_id = clone_session_snapshot(sb, sid, title)
                    st.success(f"새 세션으로 저장했습니다: {title}")
                    st.session_state.session_id = new_id
                    st.session_state.saved_msg_count = len(st.session_state.chat_history)
                    sync_chat_to_db(sb, new_id, st.session_state.chat_history)
                    st.rerun()
        with c2:
            if st.button("세션로드") and sb:
                lab = st.session_state.get("_sess_sel")
                if lab and lab in id_by_label:
                    sel = id_by_label[lab]
                    st.session_state.session_id = sel
                    st.session_state.chat_history = load_messages_from_db(sb, sel)
                    st.session_state.saved_msg_count = len(st.session_state.chat_history)
                    st.session_state.processed_files = list_vector_file_names(sb, sel)
                    st.session_state.file_hashes = load_embedding_file_hashes(sb, sel)
                    st.session_state.pending_reply = False
                    st.rerun()

        c3, c4 = st.columns(2)
        with c3:
            if st.button("세션삭제") and sb:
                lab = st.session_state.get("_sess_sel")
                if lab and lab in id_by_label:
                    delete_session_cascade(sb, id_by_label[lab])
                    reset_ui_state(sb)
                    st.rerun()
        with c4:
            if st.button("화면초기화"):
                reset_ui_state(sb)
                st.rerun()

        if st.button("vectordb"):
            st.session_state.vectordb_open = not st.session_state.vectordb_open

        st.markdown("### RAG (PDF)")
        uploads = st.file_uploader("PDF 업로드", type=["pdf"], accept_multiple_files=True)
        if st.button("파일 처리하기") and sb and ok:
            if not uploads:
                st.warning("PDF 파일을 선택하세요.")
            else:
                sid = ensure_session_row(sb)
                emb = get_embeddings()
                splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
                for uf in uploads:
                    data = uf.getvalue()
                    fh = hashlib.md5(data).hexdigest()
                    if has_file_embedding(sb, sid, fh):
                        if uf.name not in st.session_state.processed_files:
                            st.session_state.processed_files.append(uf.name)
                        st.session_state.file_hashes.add(fh)
                        continue
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(data)
                        path = tmp.name
                    try:
                        loader = PyPDFLoader(path)
                        docs = loader.load()
                        splits = splitter.split_documents(docs)
                        chunks = [d.page_content for d in splits if d.page_content.strip()]
                        if not chunks:
                            st.warning(f"{uf.name}: 텍스트를 추출하지 못했습니다.")
                            continue
                        insert_pdf_vectors(sb, emb, sid, uf.name, chunks, fh)
                        if uf.name not in st.session_state.processed_files:
                            st.session_state.processed_files.append(uf.name)
                        st.session_state.file_hashes.add(fh)
                        sync_chat_to_db(sb, sid, st.session_state.chat_history)
                    finally:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                st.success("파일 처리가 완료되었습니다. (이미 임베딩된 동일 파일은 건너뜁니다.)")

        st.text(
            f"모델: {LLM_MODEL}\n"
            f"세션 ID: {st.session_state.session_id or '없음'}\n"
            f"처리된 파일 수: {len(st.session_state.processed_files)}\n"
            f"대화 메시지 수: {len(st.session_state.chat_history)}"
        )

    if st.session_state.vectordb_open and sb and st.session_state.session_id:
        names = list_vector_file_names(sb, st.session_state.session_id)
        st.info("현재 세션 벡터 DB 파일명:\n" + ("\n".join(names) if names else "(없음)"))

    for m in st.session_state.chat_history:
        with st.chat_message(m["role"]):
            st.markdown(remove_separators(m["content"]), unsafe_allow_html=True)

    if not ok or sb is None:
        st.stop()

    if st.session_state.pending_reply:
        conv = st.session_state.chat_history
        if not conv or conv[-1]["role"] != "user":
            st.session_state.pending_reply = False
            st.rerun()
        user_text = conv[-1]["content"]
        sid = st.session_state.session_id
        emb = get_embeddings()
        llm = get_llm()

        ctx_parts: list[str] = []
        if list_vector_file_names(sb, sid):
            hits = retrieve_with_rpc(sb, emb, user_text, sid, RAG_TOP_K)
            for fn, tx in hits:
                if tx:
                    ctx_parts.append(f"[{fn}]\n{tx}")
        rag_context = "\n\n".join(ctx_parts) if ctx_parts else None
        sys_prompt = build_system_prompt(rag_context)
        hist_lc = _messages_to_lc(conv[:-1])

        with st.chat_message("assistant"):
            assistant_box = st.empty()
            full = ""
            for piece in stream_answer(llm, sys_prompt, hist_lc, user_text):
                full += piece
                assistant_box.markdown(remove_separators(full) + "▌")
            follow = make_followup_block(full, user_text)
            full_with_follow = remove_separators(full) + "\n\n" + follow
            assistant_box.markdown(full_with_follow)

        st.session_state.chat_history.append({"role": "assistant", "content": full_with_follow})
        st.session_state.pending_reply = False
        sync_chat_to_db(sb, sid, st.session_state.chat_history)
        st.session_state.saved_msg_count = len(st.session_state.chat_history)
        st.rerun()

    user_input = st.chat_input("질문을 입력하세요")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        st.session_state.pending_reply = True
        st.rerun()


if __name__ == "__main__":
    main()
