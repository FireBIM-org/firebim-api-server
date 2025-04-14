import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Table, Button, Spinner, Alert } from 'react-bootstrap';
import { getIdsFiles, IdsFileListItem, downloadIdsFile } from '../services/api';
import { formatDate } from '../utils/formatters';

const IdsFilesPage: React.FC = () => {
  const [idsFiles, setIdsFiles] = useState<IdsFileListItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  useEffect(() => {
    const fetchIdsFiles = async () => {
      try {
        setLoading(true);
        const data = await getIdsFiles();
        setIdsFiles(data);
        setError(null);
      } catch (err) {
        console.error('Failed to fetch IDS files:', err);
        setError('Failed to load your IDS files. Please try again later.');
      } finally {
        setLoading(false);
      }
    };

    fetchIdsFiles();
  }, []);

  const handleDownload = async (idsId: string, filename: string) => {
    try {
      setDownloadingId(idsId);
      const blob = await downloadIdsFile(idsId);
      
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
      console.error('Failed to download IDS file:', err);
      setError('Failed to download the IDS file. Please try again later.');
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="ids-files-page">
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1>IDS Files</h1>
        <Link to="/upload-ids" className="btn btn-primary">
          Upload New IDS File
        </Link>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      {loading ? (
        <div className="text-center my-5">
          <Spinner animation="border" role="status">
            <span className="visually-hidden">Loading...</span>
          </Spinner>
        </div>
      ) : idsFiles.length === 0 ? (
        <div className="text-center my-5">
          <p className="mb-4">You haven't uploaded any IDS files yet.</p>
          <Link to="/upload-ids" className="btn btn-primary">
            Upload Your First IDS File
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
            {idsFiles.map((idsFile) => (
              <tr key={idsFile.id}>
                <td>{idsFile.filename}</td>
                <td>{formatDate(idsFile.created_at)}</td>
                <td>
                  <div className="btn-group">
                    <Link 
                      to={`/ids/${idsFile.id}`} 
                      className="btn btn-sm btn-info"
                    >
                      View Details
                    </Link>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleDownload(idsFile.id, idsFile.filename)}
                      disabled={downloadingId === idsFile.id}
                    >
                      {downloadingId === idsFile.id ? (
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

export default IdsFilesPage;