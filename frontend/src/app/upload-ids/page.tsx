import React from 'react';
import ProtectedRoute from '../../components/ProtectedRoute';
import IdsUploadPage from '../../pages/IdsUploadPage';

export default function UploadIds() {
  return (
    <ProtectedRoute>
      <IdsUploadPage />
    </ProtectedRoute>
  );
}