import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Define directories to scan
const directories = [
  path.resolve(__dirname, '../src/pages'),
  path.resolve(__dirname, '../src/components')
];

// Process each file in the given directories
async function processFiles() {
  try {
    for (const dir of directories) {
      const files = fs.readdirSync(dir);
      
      for (const file of files) {
        if (file.endsWith('.tsx') || file.endsWith('.jsx')) {
          const filePath = path.join(dir, file);
          let content = await fs.promises.readFile(filePath, 'utf8');
          
          // Skip if already updated
          if (!content.includes('react-router-dom')) {
            console.log(`Skipping ${file} - no react-router-dom imports`);
            continue;
          }
          
          // Replace imports
          let updatedContent = content;
          
          // Replace Link import
          updatedContent = updatedContent.replace(
            /import\s+{\s*Link\s*(?:,\s*[^}]*\s*)*}\s*from\s*['"]react-router-dom['"];?/,
            (match) => {
              if (match.includes('useNavigate') || match.includes('Navigate')) {
                // If the import has useNavigate, replace with both Link and useRouter
                return 'import Link from \'next/link\';\nimport { useRouter } from \'next/navigation\';';
              } else {
                // Otherwise just replace Link
                return 'import Link from \'next/link\';';
              }
            }
          );
          
          // Replace useNavigate and Navigate imports
          updatedContent = updatedContent.replace(
            /import\s+{\s*(?:useNavigate|Navigate)(?:,\s*[^}]*\s*)*}\s*from\s*['"]react-router-dom['"];?/,
            'import { useRouter } from \'next/navigation\';'
          );
          
          // Replace Link usage: <Link to="..."> to <Link href="...">
          updatedContent = updatedContent.replace(
            /<Link\s+to=/g,
            '<Link href='
          );
          
          // Replace useNavigate with useRouter
          updatedContent = updatedContent.replace(
            /const\s+navigate\s*=\s*useNavigate\(\);/g,
            'const router = useRouter();'
          );
          
          // Replace navigate calls
          updatedContent = updatedContent.replace(
            /navigate\(['"]([^'"]+)['"]\)/g,
            'router.push(\'$1\')'
          );
          
          // Replace Navigate component with router.push
          updatedContent = updatedContent.replace(
            /<Navigate\s+to=['"]([^'"]+)['"]\s*\/>/g,
            '{ useEffect(() => { router.push(\'$1\'); }, []) }'
          );
          
          // Save the updated content
          if (content !== updatedContent) {
            await fs.promises.writeFile(filePath, updatedContent);
            console.log(`Updated ${file}`);
          } else {
            console.log(`No changes needed for ${file}`);
          }
        }
      }
    }
    
    console.log('All files processed!');
  } catch (error) {
    console.error('Error processing files:', error);
  }
}

// Run the script
processFiles();