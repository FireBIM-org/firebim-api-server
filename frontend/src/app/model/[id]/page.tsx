import React from 'react';
import ProtectedRoute from '../../../components/ProtectedRoute';
import ModelDetailsPage from '../../../pages/ModelDetailsPage';

interface ModelDetailsProps {
  params: { id: string };
}

export default async function ModelDetails({ params }: ModelDetailsProps) {
  // By marking the component as async, we ensure params are properly resolved
  const id = params.id;
  
  return (
    <ProtectedRoute>
      <ModelDetailsPage id={id} />
    </ProtectedRoute>
  );
}