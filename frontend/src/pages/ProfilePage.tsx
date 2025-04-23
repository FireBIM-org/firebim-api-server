'use client';

import React from 'react';
import { useAuth } from '../contexts/AuthContext';

const ProfilePage: React.FC = () => {
  const { user } = useAuth();

  if (!user) {
    return (
      <div className="alert alert-info">
        Loading profile information...
      </div>
    );
  }

  return (
    <div className="row">
      <div className="col-md-8 mx-auto">
        <div className="card shadow">
          <div className="card-header bg-primary text-white">
            <h3 className="mb-0">User Profile</h3>
          </div>
          <div className="card-body">
            <div className="row mb-3">
              <div className="col-sm-3">
                <h6 className="mb-0">Username</h6>
              </div>
              <div className="col-sm-9 text-secondary">
                {user.username}
              </div>
            </div>
            <div className="row mb-3">
              <div className="col-sm-3">
                <h6 className="mb-0">Email</h6>
              </div>
              <div className="col-sm-9 text-secondary">
                {user.email}
              </div>
            </div>
            <div className="row mb-3">
              <div className="col-sm-3">
                <h6 className="mb-0">Status</h6>
              </div>
              <div className="col-sm-9 text-secondary">
                {user.is_active ? (
                  <span className="badge bg-success">Active</span>
                ) : (
                  <span className="badge bg-danger">Inactive</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;