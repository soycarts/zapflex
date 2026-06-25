import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "zapflex — live",
  description: "Autonomous home-battery flexibility, run by an agent swarm.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
