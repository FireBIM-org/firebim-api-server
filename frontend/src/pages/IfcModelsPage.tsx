'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { Table, Button, Spinner, Alert } from 'react-bootstrap';
import { getIfcModels, IfcModelListItem, downloadIfcModel } from '../services/api';
import { formatDate } from '../utils/formatters';

const IfcModelsPage: React.FC = () => {
  const [models, setModels] = useState<IfcModelListItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  useEffect(() => {
    const fetchModels = async () => {
      try {
        setLoading(true);
        const data = await getIfcModels();
        setModels(data);
        setError(null);
      } catch (err) {
        console.error('Failed to fetch models:', err);
        setError('Failed to load your models. Please try again later.');
      } finally {
        setLoading(false);
      }
    };

    fetchModels();
  }, []);

  const handleDownload = async (modelId: string, filename: string) => {
    try {
      setDownloadingId(modelId);
      const blob = await downloadIfcModel(modelId);
      
      // Create download link
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.style.display = 'none';
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      
      // Clean up
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      console.error('Failed to download model:', err);
      setError('Failed to download the model. Please try again later.');
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="ifc-models-page">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1>IFC Models</h1>
        <Link href="/upload-model" className="btn btn-primary">
          Upload New Model
        </Link>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      {loading ? (
        <div className="text-center my-5">
          <Spinner animation="border" role="status">
            <span className="visually-hidden">Loading...</span>
          </Spinner>
        </div>
      ) : models.length === 0 ? (
        <div className="text-center my-5">
          <p className="mb-4">You haven't uploaded any IFC models yet.</p>
          <Link href="/upload-model" className="btn btn-primary">
            Upload Your First Model
          </Link>
        </div>
      ) : (
        <Table striped bordered hover responsive>
          <thead>
            <tr>
              <th>Filename</th>
              <th>Uploaded</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr key={model.id}>
                <td>{model.filename}</td>
                <td>{formatDate(model.created_at)}</td>
                <td>
                  <div className="btn-group">
                    <Link href={`/model/${model.id}`} 
                      className="btn btn-sm btn-info"
                    >
                      View Details
                    </Link>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleDownload(model.id, model.filename)}
                      disabled={downloadingId === model.id}
                    >
                      {downloadingId === model.id ? (
                        <>
                          <Spinner
                            as="span"
                            animation="border"
                            size="sm"
                            role="status"
                            aria-hidden="true"
                          />
                          <span className="visually-hidden">Downloading...</span>
                        </>
                      ) : (
                        'Download'
                      )}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
};

export default IfcModelsPage;