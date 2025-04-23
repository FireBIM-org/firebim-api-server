import React from 'react';
import ProtectedRoute from '../../components/ProtectedRoute';
import ModelUploadPage from '../../pages/ModelUploadPage';

export default function UploadModel() {
  return (
    <ProtectedRoute>
      <ModelUploadPage />
    </ProtectedRoute>
  );
}