import React from 'react';
import ProtectedRoute from '../../components/ProtectedRoute';
import IdsFilesPage from '../../pages/IdsFilesPage';

export default function IdsFiles() {
  return (
    <ProtectedRoute>
      <IdsFilesPage />
    </ProtectedRoute>
  );
}