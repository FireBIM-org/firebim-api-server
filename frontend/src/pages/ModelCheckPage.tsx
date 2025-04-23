'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, Button, Alert, Spinner, ListGroup, Row, Col, Badge, Tab, Tabs, Table } from 'react-bootstrap';
import { toast } from 'react-toastify';
import * as api from '../services/api';

interface ModelCheckPageProps {
  id?: string;
  idsId?: string;
  modelId?: string;
}

const ModelCheckPage: React.FC<ModelCheckPageProps> = ({ id, idsId, modelId }) => {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkResult, setCheckResult] = useState<any>(null);
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
        console.log('Check result data:', result);
        
        // Determine if the check is passing based on the actual data
        // Override summary if needed
        if (result.percent_checks_pass !== undefined) {
          const isPassingOverall = result.percent_checks_pass >= 90; // Consider 90%+ as passing
          
          if (!result.summary) {
            result.summary = {
              total_elements: result.total_checks || 0,
              passing: isPassingOverall,
              message: isPassingOverall 
                ? `${result.percent_checks_pass}% of checks passed` 
                : `Only ${result.percent_checks_pass}% of checks passed`
            };
          } else {
            // Update the existing summary with the actual pass/fail status
            result.summary.passing = isPassingOverall;
            result.summary.total_elements = result.total_checks || 0;
            result.summary.message = isPassingOverall 
              ? `${result.percent_checks_pass}% of checks passed` 
              : `Only ${result.percent_checks_pass}% of checks passed`;
          }
        } else if (!result.summary) {
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
            onClick={() => router.push("/")}
            className="btn btn-primary"
          >
            Back to Home
          </Button>
        </div>
      </div>
    );
  }

  // Calculate overall status
  const overallStatus = checkResult?.percent_checks_pass 
    ? (checkResult.percent_checks_pass >= 90) 
    : (checkResult?.summary?.passing || false);

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
              <strong>Check Date:</strong> {checkResult?.date || 'N/A'}
            </ListGroup.Item>
            <ListGroup.Item>
              <strong>Status:</strong>{' '}
              {overallStatus ? (
                <Badge bg="success">PASS</Badge>
              ) : (
                <Badge bg="danger">FAIL</Badge>
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
                  <span>{checkResult.title || 'Check Results'}</span>
                  {overallStatus ? (
                    <Badge bg="success">PASS</Badge>
                  ) : (
                    <Badge bg="danger">FAIL</Badge>
                  )}
                </div>
              </Card.Header>
              <Card.Body>
                <Row className="mb-4">
                  <Col md={6}>
                    <Card className="h-100">
                      <Card.Header>Overall Results</Card.Header>
                      <Card.Body>
                        <Table striped bordered hover>
                          <tbody>
                            <tr>
                              <td>Total Specifications</td>
                              <td>{checkResult.total_specifications || '0'}</td>
                            </tr>
                            <tr>
                              <td>Passing Specifications</td>
                              <td>{checkResult.total_specifications_pass || '0'}</td>
                            </tr>
                            <tr>
                              <td>Percent Passing</td>
                              <td>{checkResult.percent_specifications_pass || '0'}%</td>
                            </tr>
                            <tr>
                              <td>Total Checks</td>
                              <td>{checkResult.total_checks || '0'}</td>
                            </tr>
                            <tr>
                              <td>Passing Checks</td>
                              <td>{checkResult.total_checks_pass || '0'}</td>
                            </tr>
                            <tr>
                              <td>Failing Checks</td>
                              <td>{checkResult.total_checks_fail || '0'}</td>
                            </tr>
                            <tr>
                              <td>Percent Checks Passing</td>
                              <td className={checkResult.percent_checks_pass >= 90 ? "text-success fw-bold" : "text-danger"}>
                                {checkResult.percent_checks_pass || '0'}%
                              </td>
                            </tr>
                          </tbody>
                        </Table>
                      </Card.Body>
                    </Card>
                  </Col>
                  <Col md={6}>
                    <Card className="h-100">
                      <Card.Header>Specifications Status</Card.Header>
                      <Card.Body>
                        {checkResult.specifications && checkResult.specifications.length > 0 ? (
                          <Table striped bordered hover>
                            <thead>
                              <tr>
                                <th>Specification</th>
                                <th>Status</th>
                                <th>Pass Rate</th>
                              </tr>
                            </thead>
                            <tbody>
                              {checkResult.specifications.map((spec: any, index: number) => (
                                <tr key={index}>
                                  <td>{spec.name}</td>
                                  <td>
                                    {spec.status ? (
                                      <Badge bg="success">PASS</Badge>
                                    ) : (
                                      <Badge bg="danger">FAIL</Badge>
                                    )}
                                  </td>
                                  <td>{spec.percent_checks_pass}%</td>
                                </tr>
                              ))}
                            </tbody>
                          </Table>
                        ) : (
                          <p>No specifications data available</p>
                        )}
                      </Card.Body>
                    </Card>
                  </Col>
                </Row>
              </Card.Body>
            </Card>
          </Tab>
          
          <Tab eventKey="details" title="Detailed Results">
            <Card>
              <Card.Body>
                {checkResult.specifications && checkResult.specifications.length > 0 ? (
                  <div>
                    <h4>Specifications</h4>
                    {checkResult.specifications.map((spec: any, index: number) => (
                      <Card key={index} className="mb-3">
                        <Card.Header className="d-flex justify-content-between align-items-center">
                          <span>{spec.name}</span>
                          {spec.status ? (
                            <Badge bg="success">PASS</Badge>
                          ) : (
                            <Badge bg="danger">FAIL</Badge>
                          )}
                        </Card.Header>
                        <Card.Body>
                          {spec.description && <p>{spec.description}</p>}
                          <Table striped bordered hover size="sm">
                            <tbody>
                              <tr>
                                <td>Total Applicable</td>
                                <td>{spec.total_applicable}</td>
                              </tr>
                              <tr>
                                <td>Passing</td>
                                <td>{spec.total_applicable_pass}</td>
                              </tr>
                              <tr>
                                <td>Failing</td>
                                <td>{spec.total_applicable_fail}</td>
                              </tr>
                              <tr>
                                <td>Pass Rate</td>
                                <td>{spec.percent_applicable_pass}%</td>
                              </tr>
                            </tbody>
                          </Table>
                          
                          {spec.requirements && spec.requirements.length > 0 && (
                            <div className="mt-3">
                              <h5>Requirements</h5>
                              {spec.requirements.map((req: any, reqIndex: number) => (
                                <div key={reqIndex} className="mb-3 p-3 border rounded">
                                  <div className="d-flex justify-content-between">
                                    <h6>{req.label || 'Requirement'}</h6>
                                    {req.status ? (
                                      <Badge bg="success">PASS</Badge>
                                    ) : (
                                      <Badge bg="danger">FAIL</Badge>
                                    )}
                                  </div>
                                  <p>{req.description}</p>
                                  <p><strong>Pass Rate:</strong> {req.percent_pass}% ({req.total_pass}/{req.total_applicable})</p>
                                  
                                  {req.failed_entities && req.failed_entities.length > 0 && (
                                    <div>
                                      <h6 className="text-danger">Failed Entities ({req.failed_entities.length})</h6>
                                      <ul className="text-danger small">
                                        {req.failed_entities.map((entity: any, entityIndex: number) => (
                                          <li key={entityIndex} className="mb-2">
                                            {entity.reason} 
                                            <div className="text-muted">Entity: {entity.name} (ID: {entity.id})</div>
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </Card.Body>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <p>No detailed data available</p>
                )}
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
            <Link 
              href={`/model/${checkResult.ifc_model_id}`}
              className="btn btn-outline-primary"
            >
              View Model Details
            </Link>
          </Col>
        )}
        
        {checkResult?.ids_id && (
          <Col>
            <Link 
              href={`/ids/${checkResult.ids_id}`}
              className="btn btn-outline-primary"
            >
              View IDS Details
            </Link>
          </Col>
        )}
        
        <Col className="text-end">
          <Link 
            href="/"
            className="btn btn-outline-secondary"
          >
            Back to Home
          </Link>
        </Col>
      </Row>
    </div>
  );
};

export default ModelCheckPage;