import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Card, Button, Alert, Spinner, ListGroup, Row, Col, Badge, Tab, Tabs } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

const ModelCheckPage: React.FC = () => {
  const { idsId, modelId, id } = useParams<{ idsId?: string; modelId?: string; id?: string }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkResult, setCheckResult] = useState<api.CheckResponse | null>(null);
  const [idsFile, setIdsFile] = useState<api.IdsFileListItem | null>(null);
  const [ifcModel, setIfcModel] = useState<api.IfcModelListItem | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [rawResponse, setRawResponse] = useState<any>(null);

  // Fetch check result either by direct IDs or by check result ID
  useEffect(() => {
    const fetchCheckResult = async () => {
      setLoading(true);
      setError(null);
      
      try {
        let result: any;
        
        // Case 1: Using the /check/:idsId/:modelId route
        if (idsId && modelId) {
          result = await api.checkModelWithIds(idsId, modelId);
        } 
        // Case 2: Using the /model-check/:id route
        else if (id) {
          result = await api.getCheckResultById(id);
        } else {
          throw new Error('Invalid URL parameters');
        }
        
        // Keep the raw response for debugging
        setRawResponse(result);
        
        // Normalize response structure if needed
        if (!result.summary) {
          console.warn('Response missing summary, creating default');
          result.summary = {
            total_elements: 0,
            passing: false,
            message: 'No detailed results available'
          };
        }
        
        setCheckResult(result);
        
        // Now fetch the details of the IDS file and IFC model
        setLoadingDetails(true);
        try {
          // Handle cases where ids_id or ifc_model_id may not be in the result
          const idsId = result.ids_id;
          const modelId = result.ifc_model_id;
          
          if (idsId && modelId) {
            const [idsFileData, ifcModelData] = await Promise.all([
              api.getIdsFileById(idsId),
              api.getIfcModelById(modelId)
            ]);
            
            setIdsFile(idsFileData);
            setIfcModel(ifcModelData);
          } else {
            console.warn('Missing ids_id or ifc_model_id in the result');
          }
        } catch (detailsErr) {
          console.error('Failed to fetch details:', detailsErr);
          toast.warning('Some details could not be loaded');
        } finally {
          setLoadingDetails(false);
        }
      } catch (err) {
        console.error('Check error:', err);
        setError('Failed to load the check results. Please try again.');
        toast.error('Failed to load check results');
      } finally {
        setLoading(false);
      }
    };
    
    fetchCheckResult();
  }, [idsId, modelId, id]);

  if (loading) {
    return (
      <div className="text-center my-5">
        <Spinner animation="border" role="status" className="mb-3">
          <span className="visually-hidden">Loading...</span>
        </Spinner>
        <p>Loading check results...</p>
        <p className="text-muted small">This may take a moment depending on the data size.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <Alert variant="danger">{error}</Alert>
        <div className="text-center mt-3">
          <Button 
            onClick={() => window.location.href = "/"}
            className="btn btn-primary"
          >
            Back to Home
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-4">Model Check Results</h2>
      
      <Card className="mb-4">
        <Card.Body>
          <Card.Title>Check Information</Card.Title>
          
          <ListGroup variant="flush" className="mb-3">
            <ListGroup.Item>
              <strong>Check ID:</strong> {checkResult?.id || 'N/A'}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>Model:</strong> {ifcModel ? ifcModel.filename : (checkResult?.ifc_model_id || 'N/A')}
              {loadingDetails && <Spinner animation="border" size="sm" className="ms-2" />}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>IDS File:</strong> {idsFile ? idsFile.filename : (checkResult?.ids_id || 'N/A')}
              {loadingDetails && <Spinner animation="border" size="sm" className="ms-2" />}
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
        <Tabs defaultActiveKey="summary" className="mb-4">
          <Tab eventKey="summary" title="Summary">
            <Card>
              <Card.Header>
                <div className="d-flex justify-content-between align-items-center">
                  <span>Check Results</span>
                  {checkResult.summary?.passing ? (
                    <Badge bg="success">PASS</Badge>
                  ) : (
                    <Badge bg="danger">FAIL</Badge>
                  )}
                </div>
              </Card.Header>
              <Card.Body>
                <Card.Text>{checkResult.summary?.message || 'No message available'}</Card.Text>
                
                <div className="mb-3">
                  <strong>Summary:</strong>
                  <ul className="mt-2">
                    <li>Total Elements: {checkResult.summary?.total_elements || 'N/A'}</li>
                    <li>
                      Result: {checkResult.summary?.passing ? 'All checks passed' : 'Some checks failed'}
                    </li>
                  </ul>
                </div>
              </Card.Body>
            </Card>
          </Tab>
          
          <Tab eventKey="json" title="JSON Response">
            <Card>
              <Card.Body>
                <pre className="bg-light p-3 rounded" style={{ maxHeight: '500px', overflow: 'auto' }}>
                  {JSON.stringify(rawResponse || checkResult, null, 2)}
                </pre>
              </Card.Body>
            </Card>
          </Tab>
        </Tabs>
      )}
      
      <Row className="mt-4">
        {checkResult?.ifc_model_id && (
          <Col>
            <Button 
              onClick={() => window.location.href = `/model/${checkResult.ifc_model_id}`}
              variant="outline-primary"
            >
              View Model Details
            </Button>
          </Col>
        )}
        
        {checkResult?.ids_id && (
          <Col>
            <Button 
              onClick={() => window.location.href = `/ids/${checkResult.ids_id}`}
              variant="outline-primary"
            >
              View IDS Details
            </Button>
          </Col>
        )}
        
        <Col className="text-end">
          <Button 
            onClick={() => window.location.href = "/"}
            variant="outline-secondary"
          >
            Back to Home
          </Button>
        </Col>
      </Row>
    </div>
  );
};

export default ModelCheckPage;