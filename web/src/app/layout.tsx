import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'Move',
  description: 'Schnitt-Templates als Zeitstempel, deterministisch mit ffmpeg angewendet',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
