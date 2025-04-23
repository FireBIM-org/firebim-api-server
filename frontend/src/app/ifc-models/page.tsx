import React from 'react';
import ProtectedRoute from '../../components/ProtectedRoute';
import IfcModelsPage from '../../pages/IfcModelsPage';

export default function IfcModels() {
  return (
    <ProtectedRoute>
      <IfcModelsPage />
    </ProtectedRoute>
  );
}