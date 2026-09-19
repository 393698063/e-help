import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "电商帮帮",
  description: "RAG MVP chat workspace",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
