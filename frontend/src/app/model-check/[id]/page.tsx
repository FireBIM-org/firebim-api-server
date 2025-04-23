import React from 'react';
import ProtectedRoute from '../../../components/ProtectedRoute';
import ModelCheckPage from '../../../pages/ModelCheckPage';

interface ModelCheckProps {
  params: { id: string };
}

export default async function ModelCheck({ params }: ModelCheckProps) {
  // By marking the component as async, we ensure params are properly resolved
  const id = params.id;
  
  return (
    <ProtectedRoute>
      <ModelCheckPage id={id} />
    </ProtectedRoute>
  );
}