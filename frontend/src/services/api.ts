import { DefaultService } from '../client';

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
  const response = await DefaultService.uploadModelIfcModelsPost({
    body: {
      file
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to upload IFC model');
  }
  
  return response.data as ModelResponse;
};

// Download an IFC model
export const downloadIfcModel = async (modelId: string): Promise<Blob> => {
  const response = await DefaultService.getModelIfcModelsIdGet({
    path: {
      id: modelId
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to download IFC model');
  }
  
  return response.data as Blob;
};

// Upload an IDS file
export const uploadIdsFile = async (file: File): Promise<IdsResponse> => {
  const response = await DefaultService.uploadIdsIdsFilesPost({
    body: {
      file
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to upload IDS file');
  }
  
  return response.data as IdsResponse;
};

// Download an IDS file
export const downloadIdsFile = async (idsId: string): Promise<Blob> => {
  const response = await DefaultService.getIdsIdsFilesIdGet({
    path: {
      id: idsId
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to download IDS file');
  }
  
  return response.data as Blob;
};

// Get all IFC models for the current user
export const getIfcModels = async (): Promise<IfcModelListItem[]> => {
  const response = await DefaultService.getModelsIfcModelsGet();
  
  if (!response.data) {
    return [];
  }
  
  return response.data as IfcModelListItem[];
};

// Get all IDS files for the current user
export const getIdsFiles = async (): Promise<IdsFileListItem[]> => {
  const response = await DefaultService.getIdsFilesIdsFilesGet();
  
  if (!response.data) {
    return [];
  }
  
  return response.data as IdsFileListItem[];
};

// Get a single IDS file by ID
export const getIdsFileById = async (idsId: string): Promise<IdsFileListItem> => {
  const response = await DefaultService.getIdsIdsFilesIdGet({
    path: {
      id: idsId
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to get IDS file');
  }
  
  return response.data as IdsFileListItem;
};

// Get a single IFC model by ID
export const getIfcModelById = async (modelId: string): Promise<IfcModelListItem> => {
  const response = await DefaultService.getModelIfcModelsIdGet({
    path: {
      id: modelId
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to get IFC model');
  }
  
  return response.data as IfcModelListItem;
};

// Check a model against an IDS file
export const checkModelWithIds = async (idsId: string, modelId: string): Promise<CheckResponse> => {
  const response = await DefaultService.checkModelWithIdsIfcModelsModelIdCheckGet({
    path: {
      model_id: modelId
    },
    query: {
      ids_id: idsId
    }
  });
  
  if (!response.data) {
    throw new Error('Failed to check model with IDS');
  }
  
  return response.data as CheckResponse;
};

// Get a check result by ID
export const getCheckResultById = async (checkId: string): Promise<CheckResponse> => {
  // Retrieve the check result directly from localStorage
  const savedResult = localStorage.getItem(`checkResult_${checkId}`);
  
  if (savedResult) {
    try {
      return JSON.parse(savedResult) as CheckResponse;
    } catch (e) {
      console.error('Failed to parse saved check result', e);
      throw new Error('Invalid check result data');
    }
  }
  
  // Fallback: try to get it from the array of all check results
  const savedResults = localStorage.getItem('checkResults');
  if (savedResults) {
    try {
      const checkResults = JSON.parse(savedResults) as CheckResponse[];
      const checkResult = checkResults.find(check => check.id === checkId);
      if (checkResult) {
        return checkResult;
      }
    } catch (e) {
      console.error('Failed to parse saved check results', e);
    }
  }
  
  throw new Error('Check result not found');
};

// Get all check results (stub implementation)
export const getAllCheckResults = async (): Promise<CheckResponse[]> => {
  // This would typically call an API endpoint, but we're using a simplified approach
  // In a real implementation, this would call something like DefaultService.getCheckResultsChecksGet()
  
  // For now, we'll use local storage as a temporary cache for check results
  const savedResults = localStorage.getItem('checkResults');
  if (savedResults) {
    try {
      return JSON.parse(savedResults) as CheckResponse[];
    } catch (e) {
      console.error('Failed to parse saved check results', e);
    }
  }
  
  return [];
};