import axios from 'axios';

// Base API URL
const API_URL = process.env.REACT_APP_API_URL || '/api';

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
  
  const response = await axios.post<ModelResponse>(`${API_URL}/model`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IFC model
export const downloadModel = async (modelId: string): Promise<Blob> => {
  const response = await axios.get(`${API_URL}/model/${modelId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Upload an IDS file
export const uploadIds = async (file: File): Promise<IdsResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await axios.post<IdsResponse>(`${API_URL}/ids`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

// Download an IDS file
export const downloadIds = async (idsId: string): Promise<Blob> => {
  const response = await axios.get(`${API_URL}/ids/${idsId}`, {
    responseType: 'blob',
  });
  
  return response.data;
};

// Check a model against an IDS file
export const checkModelWithIds = async (idsId: string, modelId: string): Promise<CheckResponse> => {
  const response = await axios.get<CheckResponse>(`${API_URL}/ids/${idsId}/check/${modelId}`);
  return response.data;
};