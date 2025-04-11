import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { ToastContainer } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';

import Navbar from './components/Navbar';
import HomePage from './pages/HomePage';
import ModelUploadPage from './pages/ModelUploadPage';
import IdsUploadPage from './pages/IdsUploadPage';
import ModelDetailsPage from './pages/ModelDetailsPage';
import IdsDetailsPage from './pages/IdsDetailsPage';
import ModelCheckPage from './pages/ModelCheckPage';

const App: React.FC = () => {
  return (
    <div className="app-container">
      <Navbar />
      <div className="container py-4">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/upload-model" element={<ModelUploadPage />} />
          <Route path="/upload-ids" element={<IdsUploadPage />} />
          <Route path="/model/:id" element={<ModelDetailsPage />} />
          <Route path="/ids/:id" element={<IdsDetailsPage />} />
          <Route path="/check/:idsId/:modelId" element={<ModelCheckPage />} />
        </Routes>
      </div>
      <ToastContainer position="bottom-right" />
    </div>
  );
};

export default App;