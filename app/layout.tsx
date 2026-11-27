import type { Metadata } from "next";
import "./globals.css";
import { themeScript } from "./theme-script";
export const metadata: Metadata = {
  title: "Cutlist — Find the moments worth editing",
  description: "A local clip-finding workspace for freelance video editors. Turn long recordings into a shortlist backed by evidence.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: themeScript }}/></head><body>{children}</body></html>;
}
