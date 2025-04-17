import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Button, Form, Alert, Spinner } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

const ModelUploadPage: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0] || null;
    setError(null);
    
    if (selectedFile) {
      const extension = selectedFile.name.split('.').pop()?.toLowerCase();
      if (extension !== 'ifc' && extension !== 'step') {
        setError('Please select a valid IFC or STEP file');
        setFile(null);
        return;
      }
      setFile(selectedFile);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const droppedFile = e.dataTransfer.files?.[0] || null;
    setError(null);
    
    if (droppedFile) {
      const extension = droppedFile.name.split('.').pop()?.toLowerCase();
      if (extension !== 'ifc' && extension !== 'step') {
        setError('Please select a valid IFC or STEP file');
        setFile(null);
        return;
      }
      setFile(droppedFile);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    
    setUploading(true);
    setError(null);
    
    try {
      const response = await api.uploadIfcModel(file);
      toast.success('Model uploaded successfully!');
      navigate(`/model/${response.model_id}`);
    } catch (err) {
      console.error('Upload error:', err);
      setError('Failed to upload model. Please try again.');
      toast.error('Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleBrowseClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div>
      <h2 className="mb-4">Upload IFC Model</h2>
      
      <Card className="mb-4">
        <Card.Body>
          <Card.Title>Upload a Model</Card.Title>
          <Card.Text>
            Select an IFC-STEP file to upload. The file will be stored securely and processed for further analysis.
          </Card.Text>
          
          {error && <Alert variant="danger">{error}</Alert>}
          
          <div 
            className="upload-container mb-3" 
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            onClick={handleBrowseClick}
          >
            <input 
              type="file" 
              ref={fileInputRef}
              onChange={handleFileChange} 
              style={{ display: 'none' }} 
              accept=".ifc,.step"
            />
            
            {file ? (
              <p className="mb-0">Selected file: {file.name}</p>
            ) : (
              <p className="mb-0">Drag and drop your IFC or STEP file here, or click to browse</p>
            )}
          </div>
          
          <Button 
            variant="primary" 
            onClick={handleUpload} 
            disabled={!file || uploading}
            className="d-flex align-items-center"
          >
            {uploading && (
              <Spinner 
                as="span"
                animation="border"
                size="sm"
                role="status"
                aria-hidden="true"
                className="me-2"
              />
            )}
            {uploading ? 'Uploading...' : 'Upload Model'}
          </Button>
        </Card.Body>
      </Card>
      
      <Card>
        <Card.Body>
          <Card.Title>About IFC-STEP Files</Card.Title>
          <Card.Text>
            IFC (Industry Foundation Classes) is a standardized open file format for exchanging BIM data.
            STEP files (.ifc or .step) contain 3D models and associated data from building information modeling.
          </Card.Text>
        </Card.Body>
      </Card>
    </div>
  );
};

export default ModelUploadPage;