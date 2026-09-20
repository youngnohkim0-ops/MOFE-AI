import Image from "next/image";

export default function Home() {
  return (
    <div className="flex flex-col flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex flex-1 w-full max-w-3xl flex-col items-center justify-center gap-8 py-32 px-16 bg-white dark:bg-black text-center">
        <Image src="/logo.png" alt="MOFE-AI logo" width={240} height={76} priority />
        <div className="flex flex-col items-center gap-4">
          <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">
            MOFE-AI에 오신 것을 환영합니다
          </h1>
          <p className="max-w-md text-lg leading-8 text-zinc-600 dark:text-zinc-400">
            이곳은 첫 웹페이지입니다. 앞으로 이 화면을 채워나갈 예정이에요.
          </p>
        </div>
      </main>
    </div>
  );
}
