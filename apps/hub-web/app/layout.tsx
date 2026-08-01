import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Myna Person Memory Hub",
  description:
    "Myna 本地 Person Memory 的查看、接入与偏好治理中心。",
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
