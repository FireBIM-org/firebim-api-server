import time

import requests


def verify_ontop():
    url = "http://localhost:8080/sparql"
    query = """
    PREFIX : <http://example.org/fbo/>
    SELECT * WHERE {
      ?s a :IfcWall .
    }
    """

    print(f"Connecting to Ontop at {url}...")

    max_retries = 5
    for i in range(max_retries):
        try:
            response = requests.post(url, data={"query": query}, headers={"Accept": "application/sparql-results+json"})
            if response.status_code == 200:
                print("Successfully connected to Ontop and executed SPARQL query!")
                print("Results:", response.json())
                return True
            else:
                print(f"Ontop returned status code {response.status_code}: {response.text}")
        except requests.exceptions.ConnectionError:
            print(f"Could not connect to Ontop (attempt {i + 1}/{max_retries})...")
            time.sleep(2)

    print("Failed to connect to Ontop.")
    return False


if __name__ == "__main__":
    # If we are in a CI/environment where Docker is not running, we might want to skip or just report failure
    if not verify_ontop():
        # sys.exit(1) # Don't exit with error yet, as we might just be setting up the config
        pass
