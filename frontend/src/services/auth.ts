import { AuthenticationService, type User } from '../client';
import { updateAuthToken } from '../client/config';

export interface LoginCredentials {
  username: string;
  password: string;
}

export interface RegisterData {
  username: string;
  email: string;
  password: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export type UserProfile = User;

// Store token in localStorage
const storeToken = (token: string) => {
  localStorage.setItem('auth_token', token);
  updateAuthToken(token);
};

// Get token from localStorage
export const getToken = (): string | null => {
  return localStorage.getItem('auth_token');
};

// Remove token from localStorage
export const removeToken = () => {
  localStorage.removeItem('auth_token');
  updateAuthToken(null);
};

// Check if user is authenticated
export const isAuthenticated = (): boolean => {
  return !!getToken();
};

// Login
export const login = async (credentials: LoginCredentials): Promise<AuthResponse> => {
  const response = await AuthenticationService.loginForAccessTokenTokenPost({
    body: {
      username: credentials.username,
      password: credentials.password
    }
  });
  
  if (response.data && response.data.access_token) {
    storeToken(response.data.access_token);
    return response.data;
  }
  
  throw new Error('Authentication failed');
};

// Register
export const register = async (data: RegisterData): Promise<UserProfile> => {
  const response = await AuthenticationService.registerUserRegisterPost({
    body: data
  });
  
  if (!response.data) {
    throw new Error('Registration failed');
  }
  
  return response.data;
};

// Logout
export const logout = () => {
  removeToken();
};

// Get current user profile
export const getCurrentUser = async (): Promise<UserProfile> => {
  const token = getToken();
  if (!token) {
    throw new Error('No authentication token found');
  }
  
  const response = await AuthenticationService.readUsersMeUsersMeGet();
  
  if (!response.data) {
    throw new Error('Failed to get user profile');
  }
  
  return response.data;
};