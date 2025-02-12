import requests
import os

class FileServerClient:
    def __init__(self, base_url="http://127.0.0.1:7860"):
        self.base_url = base_url
        self.access_codes = {}  # Store filename to access code mapping

    def upload_file(self, file_path):
        """Upload a single file to the server"""
        if not os.path.exists(file_path):
            print(f"Error: File {file_path} not found")
            return
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            response = requests.post(f"{self.base_url}/upload/", files=files)
            result = response.json()
            
            # Store the access code
            if 'access_code' in result:
                self.access_codes[os.path.basename(file_path)] = result['access_code']
                
        return result

    def upload_multiple_files(self, file_paths):
        """Upload multiple files to the server"""
        files = []
        for file_path in file_paths:
            if os.path.exists(file_path):
                files.append(('files', open(file_path, 'rb')))
            else:
                print(f"Warning: File {file_path} not found, skipping...")
        
        response = requests.post(f"{self.base_url}/upload-multiple/", files=files)
        result = response.json()
        
        # Store access codes
        if 'files' in result:
            for file_info in result['files']:
                self.access_codes[file_info['filename']] = file_info['access_code']
        
        # Close all opened files
        for _, file_obj in files:
            file_obj.close()
            
        return result

    def list_files(self):
        """List all files on the server"""
        response = requests.get(f"{self.base_url}/files/")
        return response.json()

    def download_file(self, access_code, save_path):
        """Download a file using its access code"""
        response = requests.get(f"{self.base_url}/download/{access_code}", stream=True)
        
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        else:
            print(f"Error: {response.json()['detail']}")
            return False

    def delete_file(self, access_code):
        """Delete a file using its access code"""
        response = requests.delete(f"{self.base_url}/delete/{access_code}")
        return response.json()


def main():
    # Create client instance
    client = FileServerClient()
    
    print("\n=== File Server Client Demo ===\n")

    # 1. Upload a single file
    print("1. Testing single file upload:")
    test_file = "test_upload.txt"
    with open(test_file, "w") as f:
        f.write("This is a test file for upload!")
    
    result = client.upload_file(test_file)
    print(result)
    access_code = result['access_code']

    # 2. List files
    print("\n2. Listing all files on server:")
    files = client.list_files()
    print(files)

    # 3. Download the file using access code
    print("\n3. Downloading the file:")
    download_path = "downloaded_test.txt"
    success = client.download_file(access_code, download_path)
    if success:
        print(f"File downloaded successfully to {download_path}")

    # 4. Upload multiple files
    print("\n4. Testing multiple file upload:")
    test_file2 = "test_upload2.txt"
    with open(test_file2, "w") as f:
        f.write("This is another test file!")
    
    result = client.upload_multiple_files([test_file, test_file2])
    print(result)

    # 5. Delete files using access codes
    print("\n5. Deleting test files from server:")
    for file_info in result['files']:
        print(client.delete_file(file_info['access_code']))

    # Clean up local test files
    os.remove(test_file)
    os.remove(test_file2)
    os.remove(download_path)

if __name__ == "__main__":
    main() 