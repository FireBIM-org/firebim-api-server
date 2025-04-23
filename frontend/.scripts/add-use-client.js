import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Define pages directory
const pagesDir = path.resolve(__dirname, '../src/pages');

// Process each file in the pages directory
async function processFiles() {
  try {
    const files = fs.readdirSync(pagesDir);
    
    for (const file of files) {
      if (file.endsWith('.tsx') || file.endsWith('.jsx')) {
        const filePath = path.join(pagesDir, file);
        const content = await fs.promises.readFile(filePath, 'utf8');
        
        // Skip if already has 'use client'
        if (content.includes("'use client'") || content.includes('"use client"')) {
          console.log(`Skipping ${file} - already has 'use client'`);
          continue;
        }
        
        // Add 'use client' directive at the top of the file
        const newContent = `'use client';\n\n${content}`;
        await fs.promises.writeFile(filePath, newContent);
        console.log(`Added 'use client' to ${file}`);
      }
    }
    
    console.log('All files processed!');
  } catch (error) {
    console.error('Error processing files:', error);
  }
}

// Run the script
processFiles();