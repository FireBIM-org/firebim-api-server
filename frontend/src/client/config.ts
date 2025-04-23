'use client';

import { createConfig } from '@hey-api/client-next';
import { client } from './client.gen';

// Base API URL
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Configure the client with the base URL
export const configureClient = () => {
  // Check if we're in a browser environment before accessing localStorage
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('auth_token');
    
    const config = createConfig({
      baseUrl: API_URL,
      headers: token ? {
        Authorization: `Bearer ${token}`
      } : undefined
    });
    
    client.setConfig(config);
  } else {
    // Server-side configuration (without auth token)
    const config = createConfig({
      baseUrl: API_URL
    });
    
    client.setConfig(config);
  }
};

// Initial configuration
configureClient();

// Function to update the auth token
export const updateAuthToken = (token: string | null) => {
  if (token) {
    client.setConfig({
      headers: {
        Authorization: `Bearer ${token}`
      }
    });
  } else {
    client.setConfig({
      headers: {}
    });
  }
};