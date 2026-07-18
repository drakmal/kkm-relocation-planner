import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'KKM Relocation Planner',
  description: 'A commute planning tool for Malaysian health workers',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
