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
export interface IfcModelListItem {
  id: string;
  filename: string;
  created_at: string;
  updated_at: string;
}

export interface IdsFileListItem {
  id: string;
  filename: string;
  created_at: string;
  updated_at: string;
}

export interface ModelResponse {
  model_id: string;
}

export interface IdsResponse {
  id: string;
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
export const uploadIfcModel = async (file: File): Promise<ModelResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await apiClient.post<ModelResponse>('/ifc_model', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IFC model
export const downloadIfcModel = async (modelId: string): Promise<Blob> => {
  const response = await apiClient.get(`/ifc_model/${modelId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Upload an IDS file
export const uploadIdsFile = async (file: File): Promise<IdsResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await apiClient.post<IdsResponse>('/ids_file', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IDS file
export const downloadIdsFile = async (idsId: string): Promise<Blob> => {
  const response = await apiClient.get(`/ids_file/${idsId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Get all IFC models for the current user
export const getIfcModels = async (): Promise<IfcModelListItem[]> => {
  const response = await apiClient.get<IfcModelListItem[]>('/ifc_models');
  return response.data;
};

// Get all IDS files for the current user
export const getIdsFiles = async (): Promise<IdsFileListItem[]> => {
  const response = await apiClient.get<IdsFileListItem[]>('/ids_files');
  return response.data;
};

// Check a model against an IDS file
export const checkModelWithIds = async (idsId: string, modelId: string): Promise<CheckResponse> => {
  const response = await apiClient.get<CheckResponse>(`/ids_file/${idsId}/check/${modelId}`);
  return response.data;
};