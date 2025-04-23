'use client';

import React, { useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { Card, Button, Alert, Spinner } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

const IdsUploadPage: React.FC = () => {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0] || null;
    setError(null);
    
    if (selectedFile) {
      const extension = selectedFile.name.split('.').pop()?.toLowerCase();
      if (extension !== 'ids') {
        setError('Please select a valid IDS file (XML format)');
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
      if (extension !== 'ids') {
        setError('Please select a valid IDS file (XML format)');
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
      const response = await api.uploadIdsFile(file);
      toast.success('IDS file uploaded successfully!');
      router.push(`/ids/${response.id}`);
    } catch (err) {
      console.error('Upload error:', err);
      setError('Failed to upload IDS file. Please try again.');
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
      <h2 className="mb-4">Upload IDS File</h2>
      
      <Card className="mb-4">
        <Card.Body>
          <Card.Title>Upload an IDS File</Card.Title>
          <Card.Text>
            Select an Information Delivery Specification (IDS) file to upload. 
            These XML-based files define requirements for BIM models.
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
              accept=".ids"
            />
            
            {file ? (
              <p className="mb-0">Selected file: {file.name}</p>
            ) : (
              <p className="mb-0">Drag and drop your IDS file here, or click to browse</p>
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
            {uploading ? 'Uploading...' : 'Upload IDS File'}
          </Button>
        </Card.Body>
      </Card>
      
      <Card>
        <Card.Body>
          <Card.Title>About IDS Files</Card.Title>
          <Card.Text>
            Information Delivery Specification (IDS) is an XML-based format that defines requirements 
            for BIM models. IDS files allow you to specify what information should be present in an 
            IFC model and can be used to validate model compliance.
          </Card.Text>
        </Card.Body>
      </Card>
    </div>
  );
};

export default IdsUploadPage;