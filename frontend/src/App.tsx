import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';

import { AuthProvider } from './contexts/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Navbar from './components/Navbar';
import HomePage from './pages/HomePage';
import ModelUploadPage from './pages/ModelUploadPage';
import IdsUploadPage from './pages/IdsUploadPage';
import ModelDetailsPage from './pages/ModelDetailsPage';
import IdsDetailsPage from './pages/IdsDetailsPage';
import ModelCheckPage from './pages/ModelCheckPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import ProfilePage from './pages/ProfilePage';

const App: React.FC = () => {
  return (
    <AuthProvider>
      <div className="app-container">
        <Navbar />
        <div className="container py-4">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            
            {/* Protected routes */}
            <Route path="/profile" element={
              <ProtectedRoute>
                <ProfilePage />
              </ProtectedRoute>
            } />
            <Route path="/upload-model" element={
              <ProtectedRoute>
                <ModelUploadPage />
              </ProtectedRoute>
            } />
            <Route path="/upload-ids" element={
              <ProtectedRoute>
                <IdsUploadPage />
              </ProtectedRoute>
            } />
            <Route path="/model/:id" element={
              <ProtectedRoute>
                <ModelDetailsPage />
              </ProtectedRoute>
            } />
            <Route path="/ids/:id" element={
              <ProtectedRoute>
                <IdsDetailsPage />
              </ProtectedRoute>
            } />
            <Route path="/check/:idsId/:modelId" element={
              <ProtectedRoute>
                <ModelCheckPage />
              </ProtectedRoute>
            } />
          </Routes>
        </div>
        <ToastContainer position="bottom-right" />
      </div>
    </AuthProvider>
  );
};

export default App;