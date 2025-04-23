import React from 'react';
import { Inter } from 'next/font/google';
import 'react-toastify/dist/ReactToastify.css';
import '../index.css';

import { Providers } from './providers';
import Navbar from '../components/Navbar';
import BootstrapClient from './bootstrap-client';

// Remove direct import of client config as it's now handled client-side
// Import will happen in the providers component

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'FireBIM',
  description: 'FireBIM - IFC Model Management Application',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <BootstrapClient />
        <Providers>
          <div className="app-container">
            <Navbar />
            <div className="container py-4">
              {children}
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}