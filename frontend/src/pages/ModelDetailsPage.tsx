'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, Button, Alert, Spinner, ListGroup, Row, Col } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

interface ModelDetailsPageProps {
  id?: string;
}

const ModelDetailsPage: React.FC<ModelDetailsPageProps> = ({ id }) => {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] =useState<string | null>(null);
  const [model, setModel] = useState<api.IfcModelListItem | null>(null);

  useEffect(() => {
    const fetchModelDetails = async () => {
      if (!id) return;
      
      try {
        setLoading(true);
        const modelData = await api.getIfcModels();
        const foundModel = modelData.find(m => m.id === id);
        
        if (foundModel) {
          setModel(foundModel);
        } else {
          setError('Model not found');
        }
      } catch (err) {
        console.error('Failed to fetch model details:', err);
        setError('Failed to load model details. Please try again.');
      } finally {
        setLoading(false);
      }
    };

    fetchModelDetails();
  }, [id]);

  const handleDownload = async () => {
    if (!id || !model) return;
    
    setDownloading(true);
    setError(null);
    
    try {
      const blob = await api.downloadIfcModel(id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = model.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Model downloaded successfully!');
    } catch (err) {
      console.error('Download error:', err);
      setError('Failed to download model. Please try again.');
      toast.error('Download failed');
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <div className="text-center my-5">
        <Spinner animation="border" role="status">
          <span className="visually-hidden">Loading...</span>
        </Spinner>
      </div>
    );
  }

  if (!model) {
    return (
      <div>
        <Alert variant="warning">Model not found</Alert>
        <Link href="/ifc-models" className="btn btn-primary">
          Back to Models
        </Link>
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-4">Model Details</h2>
      
      {error && <Alert variant="danger">{error}</Alert>}
      
      <Card className="mb-4">
        <Card.Body>
          <Card.Title>Model Information</Card.Title>
          
          <ListGroup variant="flush" className="mb-3">
            <ListGroup.Item>
              <strong>Model ID:</strong> {id}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>Filename:</strong> {model.filename}
            </ListGroup.Item>
          </ListGroup>
          
          <Row>
            <Col>
              <Button 
                variant="primary" 
                onClick={handleDownload} 
                disabled={downloading}
                className="d-flex align-items-center"
              >
                {downloading && (
                  <Spinner 
                    as="span"
                    animation="border"
                    size="sm"
                    role="status"
                    aria-hidden="true"
                    className="me-2"
                  />
                )}
                {downloading ? 'Downloading...' : 'Download Model'}
              </Button>
            </Col>
            <Col className="text-end">
              <Link 
                href="/ifc-models"
                className="btn btn-outline-secondary"
              >
                Back to Models
              </Link>
            </Col>
          </Row>
        </Card.Body>
      </Card>
      
      <Card>
        <Card.Header>Check Model with IDS</Card.Header>
        <Card.Body>
          <Card.Text>
            You can validate this model against an Information Delivery Specification (IDS) file to check compliance.
          </Card.Text>
          <Button onClick={() => router.push("/upload-ids")} variant="outline-primary">
            Select an IDS File
          </Button>
        </Card.Body>
      </Card>
    </div>
  );
};

export default ModelDetailsPage;