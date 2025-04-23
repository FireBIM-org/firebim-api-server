'use client';

import React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, Row, Col, Button } from 'react-bootstrap';

const HomePage: React.FC = () => {
  const router = useRouter();
  
  return (
    <div>
      <div className="jumbotron bg-light p-5 mb-4 rounded">
        <h1 className="display-4">Welcome to FireBIM</h1>
        <p className="lead">
          A platform for managing IFC models and checking them against IDS specifications.
        </p>
        <hr className="my-4" />
        <p>
          Upload IFC models, manage IDS files, and validate your BIM data with ease.
        </p>
      </div>

      <Row className="mt-5">
        <Col md={6} className="mb-4">
          <Card className="h-100">
            <Card.Body>
              <Card.Title>Upload Models</Card.Title>
              <Card.Text>
                Upload IFC-STEP files to store and process them. 
                The system will automatically analyze your models.
              </Card.Text>
              <Button 
                onClick={() => router.push("/upload-model")} 
                variant="primary"
              >
                Upload Model
              </Button>
            </Card.Body>
          </Card>
        </Col>
        <Col md={6} className="mb-4">
          <Card className="h-100">
            <Card.Body>
              <Card.Title>Upload IDS Files</Card.Title>
              <Card.Text>
                Upload Information Delivery Specification (IDS) files to define 
                requirements for your BIM models.
              </Card.Text>
              <Button 
                onClick={() => router.push("/upload-ids")} 
                variant="primary"
              >
                Upload IDS
              </Button>
            </Card.Body>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default HomePage;