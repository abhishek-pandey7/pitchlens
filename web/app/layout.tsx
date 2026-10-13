import type { Metadata } from "next";
import { Archivo, Geist_Mono } from "next/font/google";
import "./globals.css";

// One family at two widths: narrow and heavy for scores and headings, normal width for reading.
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
});

// Only for real code (tool calls in the agent trace, metric keys).
const codeMono = Geist_Mono({
  variable: "--font-code-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "PitchLens",
  description: "Live football match intelligence, explained for every fan.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${archivo.variable} ${codeMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
