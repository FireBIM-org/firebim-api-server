import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Card, Button, Alert, Spinner, ListGroup, Row, Col } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

const ModelDetailsPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string>('model.ifc');

  const handleDownload = async () => {
    if (!id) return;
    
    setDownloading(true);
    setError(null);
    
    try {
      const blob = await api.downloadIfcModel(id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
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
              <strong>Filename:</strong> {filename}
            </ListGroup.Item>
          </ListGroup>
          
          <Row>
            <Col>
              <Button 
                variant="primary" 
                onClick={handleDownload} 
                disabled={downloading || loading}
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
              <Button 
                onClick={() => window.location.href = "/"}
                variant="outline-secondary"
              >
                Back to Home
              </Button>
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
          <Button onClick={() => window.location.href = "/upload-ids"} variant="outline-primary">
            Select an IDS File
          </Button>
        </Card.Body>
      </Card>
    </div>
  );
};

export default ModelDetailsPage;