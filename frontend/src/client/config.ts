import { createConfig } from '@hey-api/client-next';
import { client } from './client.gen';

// Base API URL
const API_URL = import.meta.env.VITE_API_URL || '/api';

// Configure the client with the base URL
export const configureClient = () => {
  const token = localStorage.getItem('auth_token');
  
  const config = createConfig({
    baseUrl: API_URL,
    headers: token ? {
      Authorization: `Bearer ${token}`
    } : undefined
  });
  
  client.setConfig(config);
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