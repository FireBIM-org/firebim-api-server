import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || '/api';

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

export interface UserProfile {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
}

// Store token in localStorage
const storeToken = (token: string) => {
  localStorage.setItem('auth_token', token);
};

// Get token from localStorage
export const getToken = (): string | null => {
  return localStorage.getItem('auth_token');
};

// Remove token from localStorage
export const removeToken = () => {
  localStorage.removeItem('auth_token');
};

// Check if user is authenticated
export const isAuthenticated = (): boolean => {
  return !!getToken();
};

// Login
export const login = async (credentials: LoginCredentials): Promise<AuthResponse> => {
  // Convert credentials to form data for OAuth2 compatibility
  const formData = new FormData();
  formData.append('username', credentials.username);
  formData.append('password', credentials.password);
  
  const response = await axios.post<AuthResponse>(`${API_URL}/token`, formData);
  if (response.data.access_token) {
    storeToken(response.data.access_token);
  }
  return response.data;
};

// Register
export const register = async (data: RegisterData): Promise<UserProfile> => {
  const response = await axios.post<UserProfile>(`${API_URL}/register`, data);
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
  
  const response = await axios.get<UserProfile>(`${API_URL}/users/me`, {
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
  
  return response.data;
};