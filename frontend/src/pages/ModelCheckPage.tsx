import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Card, Button, Alert, Spinner, ListGroup, Row, Col, Badge } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

const ModelCheckPage: React.FC = () => {
  const { idsId, modelId } = useParams<{ idsId: string; modelId: string }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkResult, setCheckResult] = useState<api.CheckResponse | null>(null);

  useEffect(() => {
    const fetchCheckResult = async () => {
      if (!idsId || !modelId) return;
      
      setLoading(true);
      setError(null);
      
      try {
        const result = await api.checkModelWithIds(idsId, modelId);
        setCheckResult(result);
      } catch (err) {
        console.error('Check error:', err);
        setError('Failed to check model against IDS file. Please try again.');
        toast.error('Check operation failed');
      } finally {
        setLoading(false);
      }
    };
    
    fetchCheckResult();
  }, [idsId, modelId]);

  if (loading) {
    return (
      <div className="text-center my-5">
        <Spinner animation="border" role="status" className="mb-3">
          <span className="visually-hidden">Loading...</span>
        </Spinner>
        <p>Checking model against IDS specification...</p>
        <p className="text-muted small">This may take a moment depending on the model size.</p>
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-4">Model Check Results</h2>
      
      {error && <Alert variant="danger">{error}</Alert>}
      
      <Card className="mb-4">
        <Card.Body>
          <Card.Title>Check Information</Card.Title>
          
          <ListGroup variant="flush" className="mb-3">
            <ListGroup.Item>
              <strong>Check ID:</strong> {checkResult?.id || 'N/A'}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>Model ID:</strong> {modelId}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>IDS ID:</strong> {idsId}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>Status:</strong>{' '}
              {checkResult?.status === 'completed' ? (
                <Badge bg="success">Completed</Badge>
              ) : checkResult?.status === 'failed' ? (
                <Badge bg="danger">Failed</Badge>
              ) : (
                <Badge bg="warning">Pending</Badge>
              )}
            </ListGroup.Item>
          </ListGroup>
        </Card.Body>
      </Card>
      
      {checkResult && (
        <Card className="mb-4">
          <Card.Header>
            <div className="d-flex justify-content-between align-items-center">
              <span>Check Results</span>
              {checkResult.summary.passing ? (
                <Badge bg="success">PASS</Badge>
              ) : (
                <Badge bg="danger">FAIL</Badge>
              )}
            </div>
          </Card.Header>
          <Card.Body>
            <Card.Text>{checkResult.summary.message}</Card.Text>
            
            <div className="mb-3">
              <strong>Summary:</strong>
              <ul className="mt-2">
                <li>Total Elements: {checkResult.summary.total_elements}</li>
                <li>
                  Result: {checkResult.summary.passing ? 'All checks passed' : 'Some checks failed'}
                </li>
              </ul>
            </div>
            
            <Row>
              <Col>
                <Button onClick={() => window.location.href = `/model/${modelId}`} variant="outline-primary">
                  View Model Details
                </Button>
              </Col>
              <Col>
                <Button onClick={() => window.location.href = `/ids/${idsId}`} variant="outline-primary">
                  View IDS Details
                </Button>
              </Col>
              <Col className="text-end">
                <Button onClick={() => window.location.href = "/"} variant="outline-secondary">
                  Back to Home
                </Button>
              </Col>
            </Row>
          </Card.Body>
        </Card>
      )}
    </div>
  );
};

export default ModelCheckPage;