import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Define directories to scan
const directories = [
  path.resolve(__dirname, '../src/pages'),
  path.resolve(__dirname, '../src/components'),
  path.resolve(__dirname, '../src/app')
];

// Process each file in the given directories
async function processFiles() {
  try {
    for (const dir of directories) {
      if (!fs.existsSync(dir)) {
        console.log(`Directory ${dir} does not exist, skipping`);
        continue;
      }
      
      const files = fs.readdirSync(dir);
      
      for (const file of files) {
        if (file.endsWith('.tsx') || file.endsWith('.jsx')) {
          const filePath = path.join(dir, file);
          let content = await fs.promises.readFile(filePath, 'utf8');
          
          // Skip if no legacy behavior
          if (!content.includes('legacyBehavior')) {
            console.log(`Skipping ${file} - no legacyBehavior props`);
            continue;
          }
          
          // Replace legacy Link patterns with modern ones
          // Pattern: <Link href="/path" passHref legacyBehavior><Component>Text</Component></Link>
          // to: <Component href="/path" as={Link}>Text</Component>
          
          let updatedContent = content.replace(
            /<Link href="([^"]+)"[^>]*legacyBehavior[^>]*>\s*<([A-Za-z0-9.]+)([^>]*)>([^<]*)<\/\2>\s*<\/Link>/g,
            '<$2 href="$1" as={Link}$3>$4</$2>'
          );
          
          // If no changes were made with the first regex, try a more general one
          if (updatedContent === content) {
            updatedContent = content.replace(
              /<Link href="([^"]+)"[^>]*legacyBehavior[^>]*>\s*<([A-Za-z0-9.]+)([^>]*)>([\s\S]*?)<\/\2>\s*<\/Link>/g,
              '<$2 href="$1" as={Link}$3>$4</$2>'
            );
          }
          
          // Save the updated content
          if (content !== updatedContent) {
            await fs.promises.writeFile(filePath, updatedContent);
            console.log(`Updated ${file}`);
          } else {
            console.log(`No changes made to ${file} (matching failed)`);
          }
        }
      }
      
      // Handle subdirectories recursively
      const subDirs = fs.readdirSync(dir, { withFileTypes: true })
        .filter(dirent => dirent.isDirectory())
        .map(dirent => path.join(dir, dirent.name));
        
      for (const subDir of subDirs) {
        // Skip node_modules and .next directories
        if (subDir.includes('node_modules') || subDir.includes('.next')) {
          continue;
        }
        
        directories.push(subDir);
      }
    }
    
    console.log('All files processed!');
  } catch (error) {
    console.error('Error processing files:', error);
  }
}

// Run the script
processFiles();