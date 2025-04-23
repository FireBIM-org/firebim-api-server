'use client';

import React from 'react';
import Link from 'next/link';
import { Navbar as BootstrapNavbar, Nav, Container, NavDropdown } from 'react-bootstrap';
import { useAuth } from '../contexts/AuthContext';

const Navbar: React.FC = () => {
  const { isAuthenticated, user, logout } = useAuth();

  return (
    <BootstrapNavbar bg="dark" variant="dark" expand="lg">
      <Container>
        <BootstrapNavbar.Brand href="/" as={Link}>FireBIM</BootstrapNavbar.Brand>
        <BootstrapNavbar.Toggle aria-controls="basic-navbar-nav" />
        <BootstrapNavbar.Collapse id="basic-navbar-nav">
          <Nav className="me-auto">
            <Nav.Link href="/" as={Link}>Home</Nav.Link>
            {isAuthenticated && (
              <>
                <NavDropdown title="Files" id="file-nav-dropdown">
                  <NavDropdown.Item href="/ifc-models" as={Link}>IFC Models</NavDropdown.Item>
                  <NavDropdown.Item href="/ids-files" as={Link}>IDS Files</NavDropdown.Item>
                </NavDropdown>
                <NavDropdown title="Upload" id="upload-nav-dropdown">
                  <NavDropdown.Item href="/upload-model" as={Link}>Upload Model</NavDropdown.Item>
                  <NavDropdown.Item href="/upload-ids" as={Link}>Upload IDS</NavDropdown.Item>
                </NavDropdown>
              </>
            )}
          </Nav>
          <Nav>
            {isAuthenticated ? (
              <NavDropdown title={user?.username || 'User'} id="user-nav-dropdown">
                <NavDropdown.Item href="/profile" as={Link}>Profile</NavDropdown.Item>
                <NavDropdown.Divider />
                <NavDropdown.Item onClick={logout}>Logout</NavDropdown.Item>
              </NavDropdown>
            ) : (
              <>
                <Nav.Link href="/login" as={Link}>Login</Nav.Link>
                <Nav.Link href="/register" as={Link}>Register</Nav.Link>
              </>
            )}
          </Nav>
        </BootstrapNavbar.Collapse>
      </Container>
    </BootstrapNavbar>
  );
};

export default Navbar;