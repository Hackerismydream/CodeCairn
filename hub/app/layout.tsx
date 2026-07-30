import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(
    "https://codecairn-memory-hub-lab.wanjiahuang0221.chatgpt.site",
  ),
  title: "CodeCairn 记忆中心",
  description:
    "CodeCairn 面向编码智能体的本地记忆系统只读原型。",
  openGraph: {
    title: "CodeCairn 记忆中心",
    description: "让 Agent 记得，也让人看得懂。",
    images: [
      {
        url: "/og.png",
        width: 1672,
        height: 941,
        alt: "CodeCairn 记忆中心",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "CodeCairn 记忆中心",
    description: "让 Agent 记得，也让人看得懂。",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
