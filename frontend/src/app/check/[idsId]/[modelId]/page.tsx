import React from 'react';
import ProtectedRoute from '../../../../components/ProtectedRoute';
import ModelCheckPage from '../../../../pages/ModelCheckPage';

interface ModelCheckWithIdsProps {
  params: { idsId: string, modelId: string };
}

export default async function ModelCheckWithIds({ params }: ModelCheckWithIdsProps) {
  // By marking the component as async, we ensure params are properly resolved
  const idsId = params.idsId;
  const modelId = params.modelId;
  
  return (
    <ProtectedRoute>
      <ModelCheckPage idsId={idsId} modelId={modelId} />
    </ProtectedRoute>
  );
}