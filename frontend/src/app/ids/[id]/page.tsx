import React from 'react';
import ProtectedRoute from '../../../components/ProtectedRoute';
import IdsDetailsPage from '../../../pages/IdsDetailsPage';

interface IdsDetailsProps {
  params: { id: string };
}

export default async function IdsDetails({ params }: IdsDetailsProps) {
  // By marking the component as async, we ensure params are properly resolved
  const id = params.id;
  
  return (
    <ProtectedRoute>
      <IdsDetailsPage id={id} />
    </ProtectedRoute>
  );
}