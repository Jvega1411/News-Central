# Batch jobs run by the worker process (worker.py). One subclass per job: translate now,
# topics and stories later. A job never holds a transaction while run() works.


class Job:
    name = ""
    batch_size = 16

    def __init__(self):
        self.reason = ""          # why available() said no, shown in the Sources view

    def available(self):
        """Dependencies and models present. Set self.reason when returning False."""
        return True

    def pending(self, store, limit):
        """The next batch of work, at most `limit` units. Empty when there is nothing to do."""
        raise NotImplementedError

    def run(self, batch):
        """Do the work outside any transaction. Returns one result per unit."""
        raise NotImplementedError

    def save(self, store, batch, results):
        """Write the results in one transaction. Returns how many units are done."""
        raise NotImplementedError
