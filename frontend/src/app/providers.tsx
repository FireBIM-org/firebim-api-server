'use client';

import React, { useEffect } from 'react';
import { ToastContainer } from 'react-toastify';
import { AuthProvider } from '../contexts/AuthContext';
import '../client/config';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      {children}
      <ToastContainer position="bottom-right" />
    </AuthProvider>
  );
}