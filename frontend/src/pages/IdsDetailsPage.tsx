import React, { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { Card, Button, Spinner, Alert, ListGroup, Badge, Modal } from 'react-bootstrap';
import { IdsFileListItem, downloadIdsFile, getIdsFileById, getIfcModels, IfcModelListItem, checkModelWithIds, CheckResponse } from '../services/api';
import { formatDate } from '../utils/formatters';

const IdsDetailsPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  
  const [idsFile, setIdsFile] = useState<IdsFileListItem | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingFile, setDownloadingFile] = useState<boolean>(false);
  
  // For model checking
  const [ifcModels, setIfcModels] = useState<IfcModelListItem[]>([]);
  const [loadingModels, setLoadingModels] = useState<boolean>(false);
  const [showCheckModal, setShowCheckModal] = useState<boolean>(false);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [checkResult, setCheckResult] = useState<CheckResponse | null>(null);
  const [checkLoading, setCheckLoading] = useState<boolean>(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    const fetchIdsFile = async () => {
      if (!id) return;
      
      try {
        setLoading(true);
        const fileData = await getIdsFileById(id);
        setIdsFile(fileData);
      } catch (err) {
        console.error('Failed to fetch IDS file details:', err);
        setError('Failed to load the IDS file details. Please try again later.');
        // Don't navigate away automatically - just show the error
      } finally {
        setLoading(false);
      }
    };

    fetchIdsFile();
  }, [id, navigate]);

  const handleDownload = async () => {
    if (!idsFile) return;
    
    try {
      setDownloadingFile(true);
      const blob = await downloadIdsFile(idsFile.id);
      
      // Create download link
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = idsFile.filename;
      document.body.appendChild(a);
      a.click();
      
      // Clean up
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      console.error('Failed to download IDS file:', err);
      setError('Failed to download the IDS file. Please try again later.');
    } finally {
      setDownloadingFile(false);
    }
  };

  const openCheckModal = async () => {
    try {
      setLoadingModels(true);
      const models = await getIfcModels();
      setIfcModels(models);
      setShowCheckModal(true);
    } catch (err) {
      console.error('Failed to fetch IFC models:', err);
      setError('Failed to load your IFC models. Please try again later.');
    } finally {
      setLoadingModels(false);
    }
  };

  const handleCheck = async () => {
    if (!id || !selectedModelId) return;
    
    try {
      setCheckLoading(true);
      setCheckError(null);
      const result = await checkModelWithIds(id, selectedModelId);
      setCheckResult(result);
    } catch (err) {
      console.error('Failed to check model against IDS:', err);
      setCheckError('Failed to perform the check. Please try again later.');
    } finally {
      setCheckLoading(false);
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

  if (error) {
    return (
      <div>
        <Alert variant="danger">{error}</Alert>
        <div className="text-center mt-3">
          <Link to="/ids-files" className="btn btn-primary">
            Back to IDS Files
          </Link>
        </div>
      </div>
    );
  }

  if (!idsFile) {
    return (
      <div>
        <Alert variant="warning">IDS file not found</Alert>
        <div className="text-center mt-3">
          <Link to="/ids-files" className="btn btn-primary">
            Back to IDS Files
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="ids-details-page">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1>IDS File Details</h1>
        <div>
          <Link to="/ids-files" className="btn btn-outline-secondary me-2">
            Back to IDS Files
          </Link>
          <Button 
            variant="primary" 
            onClick={openCheckModal}
          >
            Check Model Against This IDS
          </Button>
        </div>
      </div>

      <Card className="mb-4">
        <Card.Header>
          <h2 className="h5 mb-0">File Information</h2>
        </Card.Header>
        <Card.Body>
          <ListGroup variant="flush">
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>Filename:</strong> <span>{idsFile.filename}</span>
            </ListGroup.Item>
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>File ID:</strong> <span>{idsFile.id}</span>
            </ListGroup.Item>
            <ListGroup.Item className="d-flex justify-content-between">
              <strong>Uploaded:</strong> <span>{formatDate(idsFile.created_at)}</span>
            </ListGroup.Item>
            {idsFile.updated_at !== idsFile.created_at && (
              <ListGroup.Item className="d-flex justify-content-between">
                <strong>Last Updated:</strong> <span>{formatDate(idsFile.updated_at)}</span>
              </ListGroup.Item>
            )}
          </ListGroup>
        </Card.Body>
        <Card.Footer>
          <Button 
            variant="primary" 
            onClick={handleDownload}
            disabled={downloadingFile}
          >
            {downloadingFile ? (
              <>
                <Spinner as="span" animation="border" size="sm" role="status" aria-hidden="true" />
                <span className="ms-2">Downloading...</span>
              </>
            ) : (
              'Download IDS File'
            )}
          </Button>
        </Card.Footer>
      </Card>

      {/* Model Check Modal */}
      <Modal 
        show={showCheckModal} 
        onHide={() => {
          setShowCheckModal(false);
          setSelectedModelId(null);
          setCheckResult(null);
          setCheckError(null);
        }}
        size="lg"
      >
        <Modal.Header closeButton>
          <Modal.Title>Check Model Against IDS</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {loadingModels ? (
            <div className="text-center py-4">
              <Spinner animation="border" role="status">
                <span className="visually-hidden">Loading models...</span>
              </Spinner>
            </div>
          ) : ifcModels.length === 0 ? (
            <Alert variant="info">
              You don't have any IFC models uploaded yet. Please upload a model first.
              <div className="mt-3">
                <Link to="/upload-model" className="btn btn-primary">
                  Upload IFC Model
                </Link>
              </div>
            </Alert>
          ) : (
            <>
              <div className="mb-3">
                <h5>Select a model to check:</h5>
                <ListGroup>
                  {ifcModels.map(model => (
                    <ListGroup.Item 
                      key={model.id}
                      action
                      active={selectedModelId === model.id}
                      onClick={() => setSelectedModelId(model.id)}
                    >
                      {model.filename}
                      <div className="text-muted small">
                        Uploaded: {formatDate(model.created_at)}
                      </div>
                    </ListGroup.Item>
                  ))}
                </ListGroup>
              </div>

              {checkError && (
                <Alert variant="danger">{checkError}</Alert>
              )}

              {checkResult && (
                <div className="mt-4">
                  <Alert variant={checkResult.summary.passing ? "success" : "warning"}>
                    <Alert.Heading>
                      {checkResult.summary.passing ? "Model Passed IDS Check" : "Model Failed IDS Check"}
                    </Alert.Heading>
                    <p>{checkResult.summary.message}</p>
                    <hr />
                    <p className="mb-0">
                      Total elements checked: {checkResult.summary.total_elements}
                    </p>
                    <div className="mt-3">
                      <Badge bg={checkResult.status === 'completed' ? 'success' : 
                              checkResult.status === 'pending' ? 'warning' : 'danger'}>
                        {checkResult.status}
                      </Badge>
                    </div>
                  </Alert>
                  
                  <div className="text-center mt-3">
                    <Link 
                      to={`/model-check/${checkResult.id}`} 
                      className="btn btn-primary"
                      onClick={() => setShowCheckModal(false)}
                    >
                      View Detailed Check Results
                    </Link>
                  </div>
                </div>
              )}
            </>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setShowCheckModal(false)}>
            Close
          </Button>
          <Button 
            variant="primary" 
            onClick={handleCheck}
            disabled={!selectedModelId || checkLoading}
          >
            {checkLoading ? (
              <>
                <Spinner as="span" animation="border" size="sm" role="status" aria-hidden="true" />
                <span className="ms-2">Checking...</span>
              </>
            ) : (
              'Run Check'
            )}
          </Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
};

export default IdsDetailsPage;