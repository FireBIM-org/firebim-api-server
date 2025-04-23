'use client';

import { useEffect } from 'react';

// This component loads Bootstrap CSS on the client-side to avoid Next.js 
// hydration issues with server components
export default function BootstrapClient() {
  useEffect(() => {
    // Load Bootstrap CSS on the client side
    import('bootstrap/dist/css/bootstrap.min.css');
  }, []);

  return null;
}