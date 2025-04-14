import React from 'react';
import { Link } from 'react-router-dom';
import { Navbar as BootstrapNavbar, Nav, Container, NavDropdown } from 'react-bootstrap';
import { useAuth } from '../contexts/AuthContext';

const Navbar: React.FC = () => {
  const { isAuthenticated, user, logout } = useAuth();

  return (
    <BootstrapNavbar bg="dark" variant="dark" expand="lg">
      <Container>
        <BootstrapNavbar.Brand as={Link} to="/">FireBIM</BootstrapNavbar.Brand>
        <BootstrapNavbar.Toggle aria-controls="basic-navbar-nav" />
        <BootstrapNavbar.Collapse id="basic-navbar-nav">
          <Nav className="me-auto">
            <Nav.Link as={Link} to="/">Home</Nav.Link>
            {isAuthenticated && (
              <>
                <NavDropdown title="Files" id="file-nav-dropdown">
                  <NavDropdown.Item as={Link} to="/ifc-models">IFC Models</NavDropdown.Item>
                  <NavDropdown.Item as={Link} to="/ids-files">IDS Files</NavDropdown.Item>
                </NavDropdown>
                <NavDropdown title="Upload" id="upload-nav-dropdown">
                  <NavDropdown.Item as={Link} to="/upload-model">Upload Model</NavDropdown.Item>
                  <NavDropdown.Item as={Link} to="/upload-ids">Upload IDS</NavDropdown.Item>
                </NavDropdown>
              </>
            )}
          </Nav>
          <Nav>
            {isAuthenticated ? (
              <NavDropdown title={user?.username || 'User'} id="user-nav-dropdown">
                <NavDropdown.Item as={Link} to="/profile">Profile</NavDropdown.Item>
                <NavDropdown.Divider />
                <NavDropdown.Item onClick={logout}>Logout</NavDropdown.Item>
              </NavDropdown>
            ) : (
              <>
                <Nav.Link as={Link} to="/login">Login</Nav.Link>
                <Nav.Link as={Link} to="/register">Register</Nav.Link>
              </>
            )}
          </Nav>
        </BootstrapNavbar.Collapse>
      </Container>
    </BootstrapNavbar>
  );
};

export default Navbar;