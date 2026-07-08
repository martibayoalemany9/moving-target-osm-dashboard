import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Moving Target OSM Dashboard",
  description: "Authenticated web dashboard for moving target network telemetry"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
