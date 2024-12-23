from functools import partial
from colbert.infra.config.config import ColBERTConfig
from colbert.utils.utils import zipstar
from colbert.modeling.tokenization import (
    QueryTokenizer,
    DocTokenizer,
    tensorize_triples,
)

from colbert.data.collection import Collection
from colbert.data.queries import Queries
from colbert.data.examples import Examples

# from colbert.utils.runs import Run


class LazyBatcher:
    def __init__(
        self, config: ColBERTConfig, triples, queries, collection, rank=0, nranks=1
    ):
        self.bsize, self.accumsteps = config.bsize, config.accumsteps
        self.nway = config.nway

        self.query_tokenizer = QueryTokenizer(config)
        self.doc_tokenizer = DocTokenizer(config)
        self.tensorize_triples = partial(
            tensorize_triples, self.query_tokenizer, self.doc_tokenizer
        )
        self.position = 0

        self.triples = Examples.cast(triples, nway=self.nway).tolist(rank, nranks)
        self.queries = Queries.cast(queries)
        self.collection = Collection.cast(collection)
        assert len(self.triples) > 0, "Received no triples on which to train."
        assert len(self.queries) > 0, "Received no queries on which to train."
        assert len(self.collection) > 0, "Received no collection on which to train."

    def __iter__(self):
        return self

    def __len__(self):
        return len(self.triples)

    def __next__(self):
        """
        Returns the next batch of queries, passages, and scores.

        This method retrieves a batch of data from the triples, processes it, and returns
        the collated results. It raises a StopIteration exception when there are no more
        batches to process.

        Returns:
            tuple: A tuple containing collated queries, passages, and scores.

        Raises:
            StopIteration: If there are no more batches to process.
        """
        offset, endpos = (
            self.position,
            min(self.position + self.bsize, len(self.triples)),
        )
        self.position = endpos

        if offset + self.bsize > len(self.triples):
            raise StopIteration

        all_queries, all_passages, all_scores = [], [], []

        for position in range(offset, endpos):
            query, *pids = self.triples[position]
            pids = pids[: self.nway]

            query = self.queries[query]

            try:
                pids, scores = zipstar(pids)
            except:
                scores = []

            passages = [self.collection[pid] for pid in pids]

            all_queries.append(query)
            all_passages.extend(passages)
            all_scores.extend(scores)

        assert len(all_scores) in [0, len(all_passages)], len(all_scores)

        return self.collate(all_queries, all_passages, all_scores)

    def collate(self, queries, passages, scores):
        """
        Collates the given queries, passages, and scores into a batch for training.

        Args:
            queries (list): A list of query tensors.
            passages (list): A list of passage tensors.
            scores (list): A list of score tensors.

        Returns:
            tuple: A tuple containing tensorized triples of queries, passages, and scores.

        Raises:
            AssertionError: If the length of queries does not match the batch size (self.bsize).
            AssertionError: If the length of passages does not match the product of nway and batch size (self.nway * self.bsize).
        """
        assert len(queries) == self.bsize
        assert len(passages) == self.nway * self.bsize

        return self.tensorize_triples(
            queries, passages, scores, self.bsize // self.accumsteps, self.nway
        )

    def skip_to_batch(self, batch_idx: int, intended_batch_size: int):
        """
        Skips to the specified batch index for training.

        Args:
            batch_idx (int): The index of the batch to skip to.
            intended_batch_size (int): The intended size of each batch.

        Prints:
            A message indicating the batch index and intended batch size.

        Sets:
            self.position: The position in the dataset corresponding to the start of the specified batch.
        """
        print(
            f"Skipping to batch #{batch_idx} (with intended_batch_size = {intended_batch_size}) for training."
        )
        self.position = intended_batch_size * batch_idx
