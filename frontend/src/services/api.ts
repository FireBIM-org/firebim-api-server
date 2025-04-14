import axios from 'axios';
import { getToken } from './auth';

// Base API URL
const API_URL = process.env.REACT_APP_API_URL || '/api';

// Create axios instance with auth header interceptor
const apiClient = axios.create({
  baseURL: API_URL
});

// Add auth token to requests if available
apiClient.interceptors.request.use(config => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Types for API responses
export interface ModelResponse {
  model_id: string;
}

export interface IdsResponse {
  ids_id: string;
}

export interface CheckResponse {
  id: string;
  ifc_model_id: string;
  ids_id: string;
  status: 'pending' | 'completed' | 'failed';
  summary: {
    total_elements: number;
    passing: boolean;
    message: string;
  };
}

// API Functions

// Upload an IFC model
export const uploadModel = async (file: File): Promise<ModelResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await apiClient.post<ModelResponse>('/model', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IFC model
export const downloadModel = async (modelId: string): Promise<Blob> => {
  const response = await apiClient.get(`/model/${modelId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Upload an IDS file
export const uploadIds = async (file: File): Promise<IdsResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await apiClient.post<IdsResponse>('/ids', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IDS file
export const downloadIds = async (idsId: string): Promise<Blob> => {
  const response = await apiClient.get(`/ids/${idsId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Check a model against an IDS file
export const checkModelWithIds = async (idsId: string, modelId: string): Promise<CheckResponse> => {
  const response = await apiClient.get<CheckResponse>(`/ids/${idsId}/check/${modelId}`);
  return response.data;
};