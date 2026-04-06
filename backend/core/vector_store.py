import faiss
import numpy as np
import os
import json
from sentence_transformers import SentenceTransformer

class VectorStore:
    def __init__(self, index_file="data/vector_index.faiss", metadata_file="data/vector_metadata.json", model_name='all-MiniLM-L6-v2'):
        self.index_file = index_file
        self.metadata_file = metadata_file
        self.dimension = 384
        try:
            self.model = SentenceTransformer(model_name)
            self.dimension = self.model.get_sentence_embedding_dimension()
        except Exception as e:
            print(f"Warning: SentenceTransformer '{model_name}' failed to load ({e}). Using lightweight randomized fallback vectors.")
            self.model = None

        self.index = None
        self.metadata = []
        self._load_or_create_index()

    def _load_or_create_index(self):
        os.makedirs(os.path.dirname(self.index_file), exist_ok=True)
        if os.path.exists(self.index_file):
            self.index = faiss.read_index(self.index_file)
        else:
            self.index = faiss.IndexFlatL2(self.dimension)

        if os.path.exists(self.metadata_file):
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)

    def _save(self):
        faiss.write_index(self.index, self.index_file)
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=4)

    def ingest(self, texts: list[str], source_metadata: list[dict] = None):
        if not texts:
            return
            
        if not source_metadata:
            source_metadata = [{"source": "unknown"} for _ in texts]
            
        if len(texts) != len(source_metadata):
            raise ValueError("Length of texts must match length of source_metadata")

        # Create embeddings
        if self.model:
            embeddings = self.model.encode(texts)
            embeddings = np.array(embeddings).astype('float32')
        else:
            embeddings = np.random.rand(len(texts), self.dimension).astype('float32')
        
        # Add to index
        self.index.add(embeddings)
        
        # Add to metadata
        for i, text in enumerate(texts):
            doc_meta = source_metadata[i].copy()
            doc_meta["content"] = text
            self.metadata.append(doc_meta)
            
        self._save()

    def search(self, query: str, k: int = 5) -> list[dict]:
        if self.index.ntotal == 0:
            return []
            
        # Create embedding for query
        if self.model:
            query_embedding = self.model.encode([query])
            query_embedding = np.array(query_embedding).astype('float32')
        else:
            query_embedding = np.random.rand(1, self.dimension).astype('float32')
        
        # Search index
        distances, indices = self.index.search(query_embedding, min(k, self.index.ntotal))
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx != -1:  # -1 means no result found for that slot
                result_meta = self.metadata[idx].copy()
                result_meta["distance"] = float(distances[0][i])
                results.append(result_meta)
                
        return results

    def clear(self):
        self.index = faiss.IndexFlatL2(self.dimension)
        self.metadata = []
        self._save()
