const fs = require('fs');
const path = require('path');

const outputDir = path.join(__dirname, 'public');

if (!fs.existsSync(outputDir)){
    fs.mkdirSync(outputDir);
}

// Minimum content to create a deployable asset
fs.writeFileSync(path.join(outputDir, 'index.html'), '<h1>Build placeholder</h1>');

console.log('Static site created in public directory');