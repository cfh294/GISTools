__author__ = 'CFH'
import psycopg2, sets, heapq, sys, timeit, csv
conn = psycopg2.connect(database='njmergers', user='postgres', password=None, host='localhost', port='5433')
cursor = conn.cursor()
target = 10000

NJ_COUNTIES = ['ATLANTIC', 'BERGEN', 'BURLINGTON', 'CAMDEN', 'CAPEMAY', 'CUMBERLAND', 'ESSEX', 'GLOUCESTER', 'HUDSON',
               'HUNTERDON', 'MERCER', 'MIDDLESEX', 'MORRIS', 'MONMOUTH', 'OCEAN', 'PASSAIC', 'SALEM', 'SOMERSET',
               'SUSSEX', 'UNION', 'WARREN']
CSV_FIELDS = ['COUNTY', 'MUN', 'MUNCODE', 'POP2010', 'POPDEN2010', 'AREA', 'MERGED']
SQFT_2_SQMI = 27878400
MILES_2_FT = 5280
STATEWIDE_MIN = 66083
NJ_POP_IN_RATE = .045
THRESH = target - (NJ_POP_IN_RATE * target)

# find the maximum thresholds
cursor.execute("SELECT county, SUM(pop) AS s FROM munis GROUP BY county")
countyMaxDict = {}
for row in cursor:
    countyMaxDict[row[0]] = row[1]

class Geometry(object):
    def __init__(self, shape):
        self.shape = shape

class Area(Geometry):
    def __init__(self, population, area):
        super(Area, self).__init__('POLYGON')
        self.population = population
        self.area = area
        self.density = SQFT_2_SQMI * (self.population / self.area)

class Border(Geometry):
    def __init__(self, munis, length):
        super(Border, self).__init__('LINE')
        self.munis = munis # list of Munis
        self.length = length

    def grabOtherMuni(self, thisMuni):
        if self.munis[0] == thisMuni:
            return self.munis[1]
        elif self.munis[1] == thisMuni:
            return self.munis[0]
        else: # Muni not found in border
            return None

    def __eq__(self, other):

        if (self.munis[0] not in other.munis) or (self.munis[1] not in other.munis):
            return False
        if other.shape != 'LINE':
            return False
        if self.length != other.length:
            return False
        else:
            return True

    def nameEq(self, other):
        town1 = self.munis[0].name
        town2 = self.munis[1].name

        otherTown1 = other.munis[0].name
        otherTown2 = other.munis[1].name

        if (town1 == otherTown1 or town1 == otherTown2) and (town2 == otherTown1 or town2 == otherTown2):
            return True
        else:
            return False

    def setMuni(self, muni):
        for oldMuni in self.munis:
            if oldMuni == muni:
                other = self.grabOtherMuni(oldMuni)
                self.munis = [muni, other]

    def __cmp__(self, other):
        return cmp(self.length, other.length)

    def __str__(self):
        return '%s and %s, %s miles'%(self.munis[0].name, self.munis[1].name, (self.length / MILES_2_FT))

    # def calculateCrossCounty(self):
    #     if self.munis[0].code[:2] != self.munis[1].code[:2]: # first two digits of muncode are different,
    #         self.isCrossCounty = True                                    # cross county border indicated

class County(Area):
    def __init__(self, name):

        cursor.execute("SELECT sum(pop), sum(area) FROM munis WHERE county = '%s'"%name)
        population = 0
        area = 0
        for dataRow in [row for row in cursor]:
            population = row[0]
            area = row[1]

        super(County, self).__init__(population, area)
        self.name = name
        self.munis = []
        self.borders = set()

        ##################################################################### this works!
        cursor.execute("SELECT * FROM munis WHERE county = '%s'"%(self.name))
        for muniRow in [row for row in cursor]:
            muni = Muni(self, muniRow[2], muniRow[4], muniRow[5], muniRow[8])
            self.munis.append(muni)
        self.munis.sort()
        #####################################################################

        dups = set()
        for muni in self.munis: # creating the border objects
            cursor.execute("SELECT adjcode, length FROM borders WHERE sourcecode = '%s'"%(muni.code))
            for borderRow in [row for row in cursor]:
                nbrCode = borderRow[0]
                length = borderRow[1]
                if length > 0.0 and (muni.code[:2] == nbrCode[:2]): # only within county
                    bdrStr1 = muni.code + ':' + nbrCode
                    dups.add(bdrStr1)
                    otherMuni = self.getMuniByCode(nbrCode)
                    newBorder = Border([muni, otherMuni], length)

                    if (nbrCode + ':' + muni.code) not in dups: # check for inverse duplicates
                        self.borders.add(newBorder)
                        muni.muniBorders.add(newBorder)
                        muni.borderCount += 1
                    else:
                        muni.muniBorders.add(self.getBorderByMuniList([muni, otherMuni]))
                        muni.borderCount += 1

    # adds a new muni to the list, sorts the list
    def addMuni(self, muni):
        self.munis.append(muni)
        self.munis.sort()

    def getBorderByMuniList(self, muniList):
        i = 0
        muni1 = muniList[0]
        muni2 = muniList[1]
        for border in self.borders:
            if muni1 in border.munis and muni2 in border.munis:
                return border
        return None # No match found


    def getMuniByCode(self, code):
        for countyMuni in self.munis:
            if countyMuni.code == code:
                return countyMuni
        return None # no match found

    def __str__(self):
        muniStr = 'Municipalities:\n'
        i = 1
        for muni in self.munis:
            muniStr += "\t%d. %s\n"%(i, muni.name)
            i += 1
        return '%s COUNTY, POPULATION: %d\n'%(self.name, self.population) + muniStr

class Muni(Area):
    def __init__(self, county, name, code, pop, area):
        super(Muni, self).__init__(pop, area)
        self.county = county
        self.name = name
        self.code = code
        self.mergerPartner = None
        self.borderCount = 0
        self.muniBorders = set()

        self.oldMunCodes = set([self.code]) # if this is a merged muni, this will contain the codes of all the old munis

        # determining if this muni falls below the population minimum
        self.isCand = False
        if self.population < THRESH:
            self.isCand = True

        self.wasMerged = False

    def getLongestBorder(self):
        longest = 0
        longestBorder = None
        for border in self.muniBorders:
            if border.length > longest:
                longest = border.length
                longestBorder = border
        return longestBorder


    # if this Muni is a post-merger town, return true
    def getIsMerger(self):
        return len(self.oldMunCodes) > 1

    def setOldMunCodes(self, newSet):
        self.oldMunCodes.add(newSet)

    def __eq__(self, other):

        # checking type
        if type(other) is not type(self):
            return False

        # backward compatibility: old munis are equal to the muni they merged into
        if other.code in self.oldMunCodes:
            return True

        # NJ municipal codes are unique to their munis, if the codes are equal, then the two
        # towns must be the same
        else:
            return self.code == other.code

    def __cmp__(self, other):
        return cmp(self.population, other.population)


    def __str__(self):
        return self.name + ', Mun. Code: ' + self.code + ', Pop. (2010): ' + str(self.population)

class Merger(object):
    def __init__(self):
        return

    @staticmethod
    def merge(muni, mergeID):
        if muni.isCand: # if this muni is a candidate...

            codePrefix = muni.county.name[:3]
            longestBorder = muni.getLongestBorder()

            longestBorderPartner = longestBorder.grabOtherMuni(muni)
            longestBorderPartnersLongestBorderPartner = longestBorderPartner.getLongestBorder().grabOtherMuni(
                                                        longestBorderPartner)

            muniBordersByLength = heapq.nlargest(len(muni.muniBorders), muni.muniBorders)
            foundPartner = None

            trigger = (len(muniBordersByLength) > 1)

            if muni.borderCount <= 1: # donut hole muni, only borders one other municipality
                foundPartner = longestBorderPartner

            # if both are candidates and both share a longest border...
            elif longestBorderPartner.isCand and longestBorderPartnersLongestBorderPartner == muni:
                foundPartner = longestBorderPartner

            # if both are candidates but don't share a longest border...
            elif trigger and longestBorderPartner.isCand and longestBorderPartnersLongestBorderPartner != muni:

                secondLongestBorderPartner = muniBordersByLength[1].grabOtherMuni(muni)
                # if secondLongestBorderPartner is None:
                #     print muni
                #     for b in muni.muniBorders:
                #         print b
                if secondLongestBorderPartner.isCand: # second longest bord. partner is a candidate
                    foundPartner = secondLongestBorderPartner

                elif secondLongestBorderPartner.isCand == False and len(muniBordersByLength) >= 3:
                    thirdLongestBorderPartner = muniBordersByLength[2].grabOtherMuni(muni)
                    if thirdLongestBorderPartner.isCand:
                        foundPartner = thirdLongestBorderPartner
                    else: # to avoid thin, sprawling munis, merge the non-candidate
                        foundPartner = secondLongestBorderPartner
                else: # all else failed, just merge 'em
                    foundPartner = longestBorderPartner

            elif trigger and longestBorderPartner.isCand == False and \
                            longestBorderPartnersLongestBorderPartner != muni:

                secondLongestBorderPartner = muniBordersByLength[1].grabOtherMuni(muni)
                thirdLongestBorderPartner = None
                if len(muniBordersByLength) >= 3:
                    thirdLongestBorderPartner = muniBordersByLength[2].grabOtherMuni(muni)
                if secondLongestBorderPartner.isCand:
                    foundPartner = secondLongestBorderPartner
                elif thirdLongestBorderPartner is not None and thirdLongestBorderPartner.isCand:
                    foundPartner = thirdLongestBorderPartner
                else:
                    foundPartner = longestBorderPartner

            else:
                foundPartner = longestBorderPartner


            # grab all of the old mun codes (just 2 if this is the first merger ever between
            # the two munis
            oldCodes = set()

            # Only add the old-style NJ municipal codes
            if not muni.wasMerged:
                oldCodes.add(muni.code)
            if not foundPartner.wasMerged:
                oldCodes.add(foundPartner.code)

            oldCodes |= muni.oldMunCodes
            oldCodes |= foundPartner.oldMunCodes

            # used for reassigning of pointers for the new muni
            oldMunis = set([muni, foundPartner])

            # All of the old borders from muni and foundPartner
            oldBorders = set()
            oldBorders |= muni.muniBorders
            oldBorders |= foundPartner.muniBorders

            # Grab new field values for the new muni
            newCode = codePrefix + '_%d'%mergeID
            newName = muni.name + '-' + foundPartner.name # to be concat. with other muni names
            newPop = muni.population + foundPartner.population # to be added up with other muni pops
            newArea = muni.area + foundPartner.area # to be added up with other muni areas

            # Create object for the new muni
            newMuni = Muni(muni.county, newName, newCode, newPop, newArea)
            newMuni.oldMunCodes = oldCodes
            newMuni.wasMerged = True
            newMuni.muniBorders = oldBorders

            # Delete the border between foundPartner and muni, it is no longer valid
            deleteBorders = set()
            for b in newMuni.muniBorders:
                if b.munis[0] in oldMunis and b.munis[1] in oldMunis:
                    deleteBorders.add(b)
            for b in deleteBorders:
                newMuni.muniBorders.remove(b)

            # reassign still-valid borders to new muni
            for b in newMuni.muniBorders:
                mun1 = b.munis[0]
                mun2 = b.munis[1]
                if mun1 in oldMunis:
                    b.munis[0] = newMuni
                else:
                    b.munis[1] = newMuni

            # for m in oldMunis:
            #     newMuni.county.munis.remove(m)
            newMuni.county.munis.remove(foundPartner)
            newMuni.county.munis.remove(muni)

            newMuni.county.munis.append(newMuni)

            newMuni.county.munis = list(set(newMuni.county.munis))
            newMuni.county.munis.sort()

    @staticmethod
    def meetsThreshold(county):
        for m in county.munis:
            if m.population < THRESH:
                return False
        return True

    @staticmethod
    def fixCodes(county):
        count = 1
        codePrefix = county.name[:3]
        for m in county.munis:
            m.code = codePrefix + '_%d'%count
            count += 1

class Tests(object):
    def __init__(self):
        self.county = County('CAPE MAY')
        self.merger = Merger(self.county)
        # self.printMergerPartners()
        # self.borderCmpTests()
        # for muni in self.county.munis:
        #     print muni

        while(not self.merger.meetsThreshold()):
            mergeMunis = self.county.munis.copy()

            while mergeMunis:
                muni = mergeMunis.pop()
                if muni in self.county.munis:
                    self.merger.merge(muni)
            self.merger.fixCodes()

        for m in self.county.munis:
            print m

    def borderCmpTests(self):
        borders = []
        print 'COMPARING TWO OF PENNSVILLE\'s BORDERS'
        for muni in self.county.munis:
            if muni.name == 'PENNSVILLE TWP':
                for b in muni.muniBorders:
                    borders.append(b)
        print borders[1] # smaller
        print borders[2]

        boolList = []

        if borders[1] < borders[2]:
            boolList = ['false', 'true', 'false', 'false', 'true', 'true']
        else:
            boolList = ['true', 'false', 'false', 'true', 'false', 'true']

        print str(borders[1] >= borders[2]) + ' Should be %s'%(boolList[0])
        print str(borders[1] <= borders[2]) + ' Should be %s'%(boolList[1])
        print str(borders[1] == borders[2]) + ' Should be %s'%(boolList[2])
        print str(borders[1] > borders[2]) + ' Should be %s'%(boolList[3])
        print str(borders[1] < borders[2]) + ' Should be %s'%(boolList[4])
        print str(borders[1] != borders[2]) + ' Should be %s'%(boolList[5])

    def printMergerPartners(self):
        for muni in self.merger.county.munis:
            print '%s partners:'%muni.name
            for p in muni.mergerPartners:
                print p.name
            print '\n'

class Driver(object):
    def __init__(self):
        self.county = None # assigned based on user input

        print "Welcome to the New Jersey Municipal Merger Project!"

        self.mainMenu()
        self.mergerMenu()




    def mainMenu(self):
        print "Select an option by entering the corresponding number:"
        print "1. Execute a merger"
        print "2. Exit program"

        valid = False
        choiceInput = None

        while not valid:
            choiceInput = input("Choice: ")
            if choiceInput <= 2 and choiceInput > 0:
                valid = True
            else:
                print "Invalid Entry!"

        if choiceInput == 2:
            sys.exit()

        # if system doesn't exit, the next menu is initiated (see constructor)

    def mergerMenu(self):
        print "Please enter the corresponding number for the county you'd like to merge:"
        print "0. All of them"

        count = 1
        for county in NJ_COUNTIES:
            print "%d. %s"%(count, county)
            count += 1

        valid = False
        choiceInput = None
        while not valid:
            choiceInput = input("Choice: ")
            if choiceInput >= 0 and choiceInput <= 21:
                valid = True
            else:
                print "Invalid input!"

        if choiceInput > 0:
            self.county = County(NJ_COUNTIES[choiceInput - 1])
            print self.county.name + " chosen."
        else:
            print "You have chosen to simulate a statewide merger."

        valid = False
        maxThresh = 0
        if choiceInput == 0:
            maxThresh = STATEWIDE_MIN
        else:
            maxThresh = countyMaxDict[self.county.name]
        userThresh = 0


        while not valid:
            userThresh = input("Enter desired threshold (must be under %d): "%maxThresh)
            if userThresh < maxThresh:
                valid = True
            else:
                print "Invalid input!"

def main():
    muniCount = 0
    countyCount = 1

    newTableFields = 'county varchar, mun varchar, muncode varchar(6), pop int, popden double precision, '
    newTableFields += 'area double precision, merged int'

    jtPath = '/Users/CFH/Desktop/join_table.csv'
    newMunisPath = '/Users/CFH/Desktop/new_munis.csv'

    cursor.execute('CREATE TABLE joinKey (old char(4), new varchar(6));')
    cursor.execute('CREATE TABLE newmunis (%s)'%newTableFields)

    for county in NJ_COUNTIES:
        c = County(county)
        codePrefix = c.name[:3]
        mergeID = 1
        munis = list(c.munis)
        while not Merger.meetsThreshold(c):
            muni = munis.pop(0)
            if muni in c.munis:
                Merger.merge(muni, mergeID)
                mergeID += 1
            munis = list(c.munis)
        Merger.fixCodes(c)
        for m in c.munis:
            muniCount+=1
        countyCount+=1

        for m in c.munis:
            mergeStatus = 0
            if m.wasMerged:
                mergeStatus = 1

            for old in m.oldMunCodes:
                cursor.execute("INSERT INTO joinkey (old, new) VALUES ('%s', '%s');"%(old, m.code))
            cursor.execute("INSERT INTO newmunis VALUES ('%s', '%s', '%s', %d, %d, %d, %d)"%(m.county.name,
                                                                                             m.name, m.code,
                                                                                             m.population,
                                                                                             m.density, m.area,
                                                                                             mergeStatus))
    print
    print 'Pre-merger muni count: 565'
    print 'Post-merger muni count: %d'%muniCount
    print 'Merging geometries...'
    joinQuery = 'SELECT * FROM munis INNER JOIN joinkey ON joinkey.old=munis.muncode ORDER BY county'
    cursor.execute('CREATE TABLE munijoin AS (%s)'%joinQuery)

    stUnionQuery = 'SELECT new, ST_Union(geom) FROM munijoin GROUP BY new'
    cursor.execute('CREATE TABLE tmp AS (%s)'%stUnionQuery)

    tableName = 'njmerged' + str(target)
    joinQuery = 'SELECT * FROM newmunis INNER JOIN tmp ON tmp.new=newmunis.muncode ORDER BY county'
    cursor.execute('CREATE TABLE %s AS (%s)'%(tableName, joinQuery))

    cursor.execute('ALTER TABLE %s DROP COLUMN new'%tableName)

    cursor.execute('DROP TABLE newmunis')
    cursor.execute('DROP TABLE tmp')
    cursor.execute('DROP TABLE munijoin')
    cursor.execute('DROP TABLE joinkey')
    conn.commit()
    print 'Finished.'

if __name__ == '__main__':
    main()















