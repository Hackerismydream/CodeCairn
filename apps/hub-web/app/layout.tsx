import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CodeCairn 记忆中心",
  description:
    "CodeCairn 本地 Memory OS 的只读记忆中心。",
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
