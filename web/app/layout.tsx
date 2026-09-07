import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OSM Route Finder",
  description: "Local OSM route planning",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
